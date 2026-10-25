"""Tests des workflows GitHub Actions et des fichiers de construction du dépôt.

Ils portent sur la structure et le contenu de ``.github/``, du ``Dockerfile``, de ``.dockerignore``,
de ``.gitignore``, des fichiers de dépendances et de ``pyproject.toml``. Aucun ne lance Docker ni
n'accède au réseau.
"""

import re
import secrets
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from tecpap.config import load_settings

ROOT = Path(__file__).parent.parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
SMOKE_SCRIPT = ROOT / ".github" / "scripts" / "smoke_image.sh"
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
PATH_VARIABLES = ("DATABASE_PATH", "BACKUP_DIR", "EXPORT_DIR", "ATTACHMENTS_DIR", "WHATSAPP_MEDIA_DIR")


def _load(name):
    path = WORKFLOWS_DIR / name
    if not path.exists():
        pytest.fail(f"{name} introuvable")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _triggers(config):
    """Déclencheurs du workflow (PyYAML lit la clé « on » comme le booléen True)."""
    return config.get("on") or config.get(True, {})


def _runs(job):
    return "\n".join(step.get("run", "") for step in job.get("steps", []))


def _uses(job):
    return [step["uses"] for step in job.get("steps", []) if "uses" in step]


def _needs(job):
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else needs


def _text(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _pyproject():
    return tomllib.loads(_text("pyproject.toml"))


@pytest.fixture
def ci_config():
    return _load("ci.yml")


@pytest.fixture
def deploy_config():
    return _load("deploy.yml")


@pytest.fixture
def all_configs():
    configs = {}
    for workflow_file in WORKFLOWS_DIR.glob("*.yml"):
        with open(workflow_file, encoding="utf-8") as f:
            configs[workflow_file.name] = yaml.safe_load(f)
    return configs


class TestWorkflowFilesExist:
    """Fichiers présents."""

    def test_workflows_directory_exists(self):
        assert WORKFLOWS_DIR.exists()

    def test_ci_workflow_exists(self):
        assert (WORKFLOWS_DIR / "ci.yml").exists()

    def test_deploy_workflow_exists(self):
        assert (WORKFLOWS_DIR / "deploy.yml").exists()

    def test_smoke_script_exists(self):
        assert SMOKE_SCRIPT.is_file()

    def test_no_backup_workflow(self):
        """Un exécuteur d'intégration continue n'a pas accès à la base de production."""
        assert not (WORKFLOWS_DIR / "backup.yml").exists()


class TestCIWorkflow:
    """Workflow d'intégration continue."""

    def test_ci_has_name(self, ci_config):
        assert ci_config["name"]

    def test_ci_has_trigger_on_push(self, ci_config):
        assert "push" in _triggers(ci_config)

    def test_ci_has_trigger_on_pull_request(self, ci_config):
        assert "pull_request" in _triggers(ci_config)

    def test_ci_has_jobs(self, ci_config):
        assert {"lint", "tests", "security", "build"} <= set(ci_config["jobs"])

    def test_ci_has_lint_job(self, ci_config):
        lint = ci_config["jobs"]["lint"]
        assert "ruff check ." in _runs(lint)
        assert not any(step.get("continue-on-error") for step in lint["steps"])

    def test_ruff_comes_from_the_locked_versions(self, ci_config):
        assert "grep '^ruff==' requirements-dev.txt" in _runs(ci_config["jobs"]["lint"])

    def test_ci_has_test_job(self, ci_config):
        assert "tests" in ci_config["jobs"]

    def test_bandit_publishes_its_report_without_blocking(self, ci_config):
        job = ci_config["jobs"]["security"]
        bandit = [step for step in job["steps"] if step.get("run", "").startswith("bandit -r")]
        assert len(bandit) == 1
        assert bandit[0]["continue-on-error"] is True
        assert "--output bandit.txt" in bandit[0]["run"]
        uploads = [step for step in job["steps"] if "upload-artifact" in step.get("uses", "")]
        assert [step["with"]["path"] for step in uploads] == ["bandit.txt"]
        assert uploads[0]["if"] == "always()"
        assert "GITHUB_STEP_SUMMARY" in _runs(job)

    def test_pip_audit_blocks_on_the_versions_of_the_image(self, ci_config):
        job = ci_config["jobs"]["security"]
        audits = [step for step in job["steps"] if step.get("run", "").startswith("pip-audit")]
        assert len(audits) == 1
        assert not audits[0].get("continue-on-error")
        assert "-r requirements.txt" in audits[0]["run"]
        assert "--no-deps" in audits[0]["run"]

    def test_security_tools_come_from_the_locked_versions(self, ci_config):
        runs = _runs(ci_config["jobs"]["security"])
        assert "pip install -r requirements-dev.txt" in runs
        assert not re.search(r"pip install\s+(?!-)\S*(bandit|pip-audit)", runs)

    def test_ci_test_job_has_matrix(self, ci_config):
        matrix = ci_config["jobs"]["tests"]["strategy"]["matrix"]
        assert matrix["python-version"] == ["3.11", "3.12", "3.13"]

    def test_tested_versions_match_the_supported_versions(self, ci_config):
        project = _pyproject()["project"]
        matrix = ci_config["jobs"]["tests"]["strategy"]["matrix"]["python-version"]
        assert project["requires-python"] == f">={matrix[0]}"
        classifiers = [entry.removeprefix("Programming Language :: Python :: ") for entry in project["classifiers"]
                       if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", entry)]
        assert classifiers == matrix

    def test_ci_test_runs_pytest(self, ci_config):
        runs = _runs(ci_config["jobs"]["tests"])
        assert "pip install -r requirements-dev.txt" in runs
        assert "pytest" in runs
        assert "--cov=tecpap" in runs

    def test_ci_build_creates_app_and_image(self, ci_config):
        build = ci_config["jobs"]["build"]
        runs = _runs(build)
        assert "create_app()" in runs
        assert "docker build" in runs
        app_step = next(step for step in build["steps"] if "create_app()" in step.get("run", ""))
        assert app_step["env"]["APP_ENV"] == "test"

    def test_ci_build_starts_the_image_it_built(self, ci_config):
        steps = ci_config["jobs"]["build"]["steps"]
        built = next(index for index, step in enumerate(steps) if "docker build" in step.get("run", ""))
        probed = next(index for index, step in enumerate(steps) if "smoke_image.sh" in step.get("run", ""))
        assert built < probed
        image = re.search(r"--tag (tecpap-orders:\$\{\{ [\w.]+ \}\})", steps[built]["run"]).group(1)
        assert steps[probed]["run"].endswith(image)

    def test_ci_uses_checkout_action(self, ci_config):
        for name, job in ci_config["jobs"].items():
            assert any("actions/checkout" in action for action in _uses(job)), name

    def test_ci_uses_setup_python_action(self, ci_config):
        for name, job in ci_config["jobs"].items():
            assert any("actions/setup-python" in action for action in _uses(job)), name


class TestSmokeScript:
    """Démarrage de l'image Docker par la sonde ``smoke_image.sh``."""

    @pytest.fixture
    def script(self):
        return SMOKE_SCRIPT.read_text(encoding="utf-8")

    def test_script_fails_on_any_error(self, script):
        assert "set -euo pipefail" in script

    def test_script_probes_healthz(self, script):
        assert "/healthz" in script
        assert "curl --silent --fail" in script

    def test_script_starts_the_image_with_a_secret_key(self, script):
        assert "SECRET_KEY=${secret_key}" in script
        assert "openssl rand -hex 32" in script

    def test_script_starts_the_image_with_an_env_file_built_from_the_example(self, script):
        assert 'cp .env.example "$env_file"' in script
        assert '--env-file "$env_file"' in script

    def test_script_checks_that_the_database_stays_under_data(self, script):
        assert "test -f /data/db/orders.db" in script

    def test_script_removes_its_containers(self, script):
        assert "trap cleanup EXIT" in script
        assert "docker rm --force" in script

    def test_script_shows_the_container_log_on_failure(self, script):
        assert "docker logs" in script


class TestDeployWorkflow:
    """Workflow de publication."""

    def test_deploy_has_name(self, deploy_config):
        assert deploy_config["name"]

    def test_deploy_triggers_on_tags(self, deploy_config):
        assert _triggers(deploy_config)["push"]["tags"] == ["v*"]

    def test_deploy_has_manual_trigger(self, deploy_config):
        assert "workflow_dispatch" in _triggers(deploy_config)

    def test_deploy_runs_tests(self, deploy_config):
        jobs = deploy_config["jobs"]
        assert "pytest" in _runs(jobs["tests"])
        assert "tests" in _needs(jobs["image"])

    def test_deploy_audits_the_dependencies_before_publishing(self, deploy_config):
        runs = _runs(deploy_config["jobs"]["tests"])
        assert "pip-audit -r requirements.txt --no-deps" in runs
        assert not any(step.get("continue-on-error") for step in deploy_config["jobs"]["tests"]["steps"])

    def test_deploy_builds_and_uploads_image(self, deploy_config):
        image = deploy_config["jobs"]["image"]
        assert "docker build" in _runs(image)
        assert "docker save" in _runs(image)
        assert any("upload-artifact" in action for action in _uses(image))

    def test_deploy_starts_the_image_before_publishing_it(self, deploy_config):
        runs = [step.get("run", "") for step in deploy_config["jobs"]["image"]["steps"]]
        probed = next(index for index, run in enumerate(runs) if "smoke_image.sh" in run)
        saved = next(index for index, run in enumerate(runs) if "docker save" in run)
        assert probed < saved

    def test_image_tag_and_artifact_name_do_not_use_the_raw_reference(self, deploy_config):
        """Un nom de branche comme ``feature/rag-chatbot`` n'est ni une étiquette Docker ni un nom d'artefact."""
        steps = deploy_config["jobs"]["image"]["steps"]
        tag_steps = [step for step in steps if step.get("id") == "tag"]
        assert len(tag_steps) == 1
        assert "tr -c 'A-Za-z0-9_.-' '-'" in tag_steps[0]["run"]
        for step in steps:
            if step is not tag_steps[0]:
                assert "ref_name" not in yaml.dump(step), step.get("name")
        assert "steps.tag.outputs.value" in yaml.dump(steps)


class TestYAMLValidity:
    """Validité des fichiers YAML."""

    def test_all_workflows_valid_yaml(self, all_configs):
        assert all_configs
        assert all(isinstance(config, dict) for config in all_configs.values())

    def test_workflows_have_required_keys(self, all_configs):
        for name, config in all_configs.items():
            assert "name" in config, name
            assert "jobs" in config, name
            assert _triggers(config), name


class TestWorkflowBestPractices:
    """Bonnes pratiques communes à tous les workflows."""

    def test_workflows_pin_every_action_to_a_commit(self, all_configs):
        """Chaque action est désignée par l'empreinte de son commit, avec sa version en commentaire."""
        for name, config in all_configs.items():
            for job in config["jobs"].values():
                for action in _uses(job):
                    assert SHA_PIN.match(action), f"{action} ({name}) sans empreinte de commit"
            for line in _text(f".github/workflows/{name}").splitlines():
                if "uses:" in line:
                    assert re.search(r"@[0-9a-f]{40} # v\d+(\.\d+){0,2}$", line.strip()), line

    def test_workflows_have_descriptive_names(self, all_configs):
        for name, config in all_configs.items():
            assert len(config.get("name", "")) > 1, name

    def test_jobs_have_names(self, all_configs):
        for name, config in all_configs.items():
            for job_id, job in config["jobs"].items():
                assert job.get("name"), f"{name}/{job_id}"

    def test_jobs_have_a_time_limit(self, all_configs):
        """Un test qui reste bloqué ne doit pas occuper un exécuteur pendant les six heures par défaut."""
        for name, config in all_configs.items():
            for job_id, job in config["jobs"].items():
                limit = job.get("timeout-minutes")
                assert isinstance(limit, int) and 0 < limit <= 60, f"{name}/{job_id}"

    def test_workflows_only_read_the_repository(self, all_configs):
        for name, config in all_configs.items():
            assert config.get("permissions") == {"contents": "read"}, name

    def test_checkout_does_not_keep_the_token(self, all_configs):
        for name, config in all_configs.items():
            for job in config["jobs"].values():
                for step in job["steps"]:
                    if "actions/checkout" in step.get("uses", ""):
                        assert step["with"]["persist-credentials"] is False, name


class TestSecurityBestPractices:
    """Secrets et variables d'environnement."""

    def test_no_hardcoded_real_secrets(self, all_configs):
        dumped = yaml.dump(all_configs).lower()
        for pattern in ("sk-", "ghp_", "password=", "auth_token="):
            assert pattern not in dumped, pattern

    def test_uses_environment_variables(self, ci_config):
        assert ci_config["env"]["PYTHON_VERSION"]

    def test_ci_needs_no_secrets(self):
        """Les tests tournent sans aucun secret : services externes simulés."""
        assert "secrets." not in (WORKFLOWS_DIR / "ci.yml").read_text(encoding="utf-8")


class TestWorkflowDependencies:
    """Ordre des jobs."""

    def test_test_depends_on_lint(self, ci_config):
        assert "lint" in _needs(ci_config["jobs"]["tests"])

    def test_security_depends_on_lint(self, ci_config):
        assert "lint" in _needs(ci_config["jobs"]["security"])

    def test_build_depends_on_test(self, ci_config):
        assert "tests" in _needs(ci_config["jobs"]["build"])

    def test_build_depends_on_security(self, ci_config):
        assert "security" in _needs(ci_config["jobs"]["build"])


# ---------------------------------------------------------------- dépendances


def _requirements(name):
    """Exigences d'un fichier de dépendances, sans les commentaires ni les lignes vides."""
    lines = (line.split(" #", 1)[0].strip() for line in _text(name).splitlines())
    return [Requirement(line) for line in lines if line and not line.startswith("#")]


def _applicable(requirements):
    """Version figée de chaque paquet pour l'interpréteur et le système courants."""
    return {canonicalize_name(requirement.name): str(next(iter(requirement.specifier)).version)
            for requirement in requirements
            if requirement.marker is None or requirement.marker.evaluate()}


class TestDependencyFiles:
    """Versions exactes des dépendances, installées à l'identique en développement, en CI et dans l'image."""

    @pytest.mark.parametrize("name", ["requirements.txt", "requirements-dev.txt"])
    def test_every_dependency_has_an_exact_version(self, name):
        requirements = _requirements(name)
        assert requirements
        for requirement in requirements:
            operators = [specifier.operator for specifier in requirement.specifier]
            assert operators == ["=="], requirement.name
            assert not requirement.extras, requirement.name

    @pytest.mark.parametrize("name", ["requirements.txt", "requirements-dev.txt"])
    def test_files_are_generated_from_pyproject(self, name):
        header = _text(name).splitlines()[:3]
        assert header[0].startswith("# This file was autogenerated by uv")
        assert "uv pip compile pyproject.toml" in header[1]
        assert f"-o {name}" in header[1]

    def test_development_file_contains_the_runtime_versions(self):
        runtime = {(str(r.name), str(r.specifier), str(r.marker)) for r in _requirements("requirements.txt")}
        development = {(str(r.name), str(r.specifier), str(r.marker)) for r in _requirements("requirements-dev.txt")}
        assert runtime <= development

    def test_locked_versions_satisfy_the_declared_ranges(self):
        project = _pyproject()["project"]
        for file_name, declared in (("requirements.txt", project["dependencies"]),
                                    ("requirements-dev.txt", project["dependencies"]
                                     + project["optional-dependencies"]["dev"])):
            locked = _applicable(_requirements(file_name))
            for text in declared:
                requirement = Requirement(text)
                if requirement.marker is not None and not requirement.marker.evaluate():
                    continue
                version = locked.get(canonicalize_name(requirement.name))
                assert version is not None, f"{requirement.name} absent de {file_name}"
                assert requirement.specifier.contains(version, prereleases=True), (
                    f"{requirement.name} {version} hors de {requirement.specifier} ({file_name})")

    def test_time_zone_database_is_locked(self):
        """``zoneinfo`` lit ses fuseaux dans ce paquet quand le système n'en fournit pas (Windows)."""
        assert "tzdata" in _applicable(_requirements("requirements.txt"))

    def test_development_tools_are_exact_in_pyproject(self):
        dev = _pyproject()["project"]["optional-dependencies"]["dev"]
        exact = {Requirement(text).name for text in dev if "==" in text}
        assert {"ruff", "bandit", "pip-audit"} <= exact

    def test_development_file_installs_the_tools_the_workflows_call(self):
        locked = _applicable(_requirements("requirements-dev.txt"))
        assert {"pytest", "pytest-cov", "ruff", "bandit", "pip-audit"} <= set(locked)

    def test_runtime_file_has_no_test_tools(self):
        locked = _applicable(_requirements("requirements.txt"))
        assert not {"pytest", "pytest-cov", "ruff", "bandit", "pip-audit"} & set(locked)


class TestPyproject:
    """Réglages de ``pyproject.toml`` communs au code, aux tests et à l'image."""

    def test_python_version_matches_the_image(self):
        project = _pyproject()
        minimum = project["project"]["requires-python"].removeprefix(">=")
        assert re.search(rf"^FROM python:{re.escape(minimum)}-slim$", _text("Dockerfile"), re.MULTILINE)
        assert project["tool"]["ruff"]["target-version"] == "py" + minimum.replace(".", "")

    def test_pytest_rejects_unknown_markers(self):
        assert "--strict-markers" in _pyproject()["tool"]["pytest"]["ini_options"]["addopts"]

    def test_pytest_dumps_the_stack_of_a_test_that_hangs(self):
        timeout = _pyproject()["tool"]["pytest"]["ini_options"]["faulthandler_timeout"]
        assert 0 < float(timeout) <= 300

    def test_every_declared_marker_is_used(self):
        """Un marqueur que personne n'utilise laisserait croire que ``-m`` sélectionne des tests."""
        declared = {entry.split(":", 1)[0]
                    for entry in _pyproject()["tool"]["pytest"]["ini_options"].get("markers", [])}
        used = set()
        for path in (ROOT / "tests").glob("*.py"):
            used |= set(re.findall(r"pytest\.mark\.(\w+)", path.read_text(encoding="utf-8")))
        assert declared <= used


# ---------------------------------------------------------------- Dockerfile et fichiers ignorés


def _dockerfile_env():
    """Variables définies par les instructions ENV du Dockerfile."""
    text = re.sub(r"\\\n", " ", _text("Dockerfile"))
    env = {}
    for line in text.splitlines():
        if line.startswith("ENV "):
            env.update(pair.split("=", 1) for pair in line.removeprefix("ENV ").split())
    return env


def _env_file(name):
    """Variables d'un fichier lu par ``docker run --env-file`` : une ligne ``NOM=valeur``, valeur littérale."""
    env = {}
    for line in _text(name).splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env


class TestDockerfile:
    """Image de production."""

    def test_data_paths_are_defined_under_data(self):
        env = _dockerfile_env()
        assert env["APP_ENV"] == "production"
        for variable in PATH_VARIABLES:
            assert env[variable].startswith("/data/"), variable

    def test_application_reads_the_variables_of_the_image(self):
        settings = load_settings(env={**_dockerfile_env(), "SECRET_KEY": secrets.token_hex(32)})
        assert settings.env == "production"
        assert settings.database_path == "/data/db/orders.db"
        assert (settings.backup_dir, settings.export_dir) == ("/data/backups", "/data/exports")
        assert (settings.attachments_dir, settings.whatsapp_media_dir) == ("/data/media/attachments",
                                                                           "/data/media/whatsapp")

    def test_data_directories_exist_and_belong_to_the_application_user(self):
        text = _text("Dockerfile")
        for directory in {"/data/db", "/data/backups", "/data/exports", "/data/media/attachments",
                          "/data/media/whatsapp"}:
            assert directory in text.split("mkdir -p", 1)[1].split("&&", 1)[0], directory
        assert "chown -R tecpap:tecpap /data" in text
        assert re.search(r"^USER tecpap$", text, re.MULTILINE)

    def test_volumes_cover_the_database_the_backups_and_the_media(self):
        assert 'VOLUME ["/data/db", "/data/backups", "/data/media"]' in _text("Dockerfile")

    def test_env_file_copied_from_the_example_does_not_move_the_data(self):
        """``docker run --env-file .env`` remplace les variables de l'image : le modèle ne doit donc fixer
        aucun chemin de données, ni l'environnement d'exécution."""
        example = _env_file(".env.example")
        assert not {*PATH_VARIABLES, "APP_ENV"} & set(example)
        env = {**_dockerfile_env(), **example, "SECRET_KEY": secrets.token_hex(32)}
        settings = load_settings(env=env)
        assert settings.env == "production"
        assert settings.database_path == _dockerfile_env()["DATABASE_PATH"]

    def test_dependencies_come_from_the_locked_file_before_the_code_is_copied(self):
        text = _text("Dockerfile")
        assert text.index("COPY requirements.txt .") < text.index("RUN pip install -r requirements.txt")
        assert text.index("RUN pip install -r requirements.txt") < text.index("COPY . .")

    def test_one_process_with_threads(self):
        text = _text("Dockerfile")
        command = text[text.index("CMD ["):]
        assert '"--workers", "1"' in command
        assert re.search(r'"--threads", "[2-9]\d*"', command)
        assert '"app:app"' in command

    def test_single_process_is_justified_by_the_shared_state(self):
        """Le commentaire explique le processus unique par l'état en mémoire, pas par une connexion partagée."""
        text = _text("Dockerfile")
        comment = " ".join(line.removeprefix("# ") for line in
                           text[text.index("# Un seul processus"):text.index("CMD [")].splitlines())
        assert "connexion unique" not in comment
        assert "planificateur de sauvegardes" in comment
        assert "propre connexion SQLite" in comment

    def test_stop_signal_has_time_to_finish_the_running_tasks(self):
        text = _text("Dockerfile")
        assert '"--graceful-timeout", "120"' in text
        assert "--stop-timeout 130" in text

    def test_healthcheck_probes_healthz(self):
        assert "/healthz" in _text("Dockerfile").split("HEALTHCHECK", 1)[1].split("CMD [", 1)[0]


def _dockerignore_patterns():
    return [line.strip() for line in _text(".dockerignore").splitlines() if line.strip() and not line.startswith("#")]


def _regex_of(pattern):
    """Expression régulière d'un motif de ``.dockerignore`` (``*`` ne franchit pas ``/``, ``**`` le franchit)."""
    regex, index = "", 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            regex, index = regex + "(?:.*/)?", index + 3
        elif pattern.startswith("**", index):
            regex, index = regex + ".*", index + 2
        elif pattern[index] == "*":
            regex, index = regex + "[^/]*", index + 1
        elif pattern[index] == "?":
            regex, index = regex + "[^/]", index + 1
        elif pattern[index] == "[":
            end = pattern.index("]", index)
            regex, index = regex + pattern[index:end + 1], end + 1
        else:
            regex, index = regex + re.escape(pattern[index]), index + 1
    return re.compile(regex)


def _is_sent_to_the_daemon(path):
    """Vrai si ``path`` reste dans le contexte de construction : un fichier est exclu quand un motif désigne
    lui-même ou l'un de ses dossiers parents ; un motif ``!`` le réintègre, le dernier motif qui s'applique
    l'emporte."""
    parts = path.split("/")
    candidates = ["/".join(parts[:count]) for count in range(1, len(parts) + 1)]
    excluded = False
    for pattern in _dockerignore_patterns():
        negated = pattern.startswith("!")
        regex = _regex_of(pattern.removeprefix("!").strip("/"))
        if any(regex.fullmatch(candidate) for candidate in candidates):
            excluded = not negated
    return not excluded


class TestDockerignore:
    """Contexte de construction de l'image."""

    @pytest.mark.parametrize("path", [
        ".env", ".env.production", "scripts/.env", "config/.env.local",
        "orders.db", "scripts/clients.db", "knowledge/index.db", "evaluation/results/x.db", "data/orders.db-wal",
        "backups/backup_1.db.gz", "whatsapp_media/audio.ogg", "attachments/bon.pdf", "exports/commandes.xlsx",
        "__pycache__/app.cpython-311.pyc", "tecpap/__pycache__/config.cpython-311.pyc",
        "tecpap/web/routes/__pycache__/api.cpython-311.pyc", "scripts/module.pyc",
        ".git/config", ".github/workflows/ci.yml", ".venv/Scripts/python.exe",
        "tests/test_config.py", "evaluation/run_eval.py", "evaluation/dataset/messages.jsonl",
        "requirements-dev.txt", ".coverage", "coverage.xml",
    ])
    def test_file_stays_out_of_the_image(self, path):
        assert not _is_sent_to_the_daemon(path)

    @pytest.mark.parametrize("path", [
        "app.py", "requirements.txt", "pyproject.toml", "tecpap/config.py", "tecpap/web/templates/base.html",
        "tecpap/web/static/images/tecpap-logo.png", "scripts/hash_password.py", "knowledge/produits.md", ".env.example",
    ])
    def test_file_is_sent_to_the_image(self, path):
        assert _is_sent_to_the_daemon(path)

    def test_patterns_apply_at_every_depth(self):
        """Sans préfixe ``**/``, un motif comme ``*.db`` ne vaut que pour la racine du contexte."""
        for pattern in _dockerignore_patterns():
            pattern = pattern.removeprefix("!")
            if pattern.startswith("*"):
                assert pattern.startswith("**/"), pattern


def _is_ignored_by_git(path):
    completed = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--no-index", "--quiet", path],
                               capture_output=True, check=False, timeout=60)
    assert completed.returncode in (0, 1), completed.stderr
    return completed.returncode == 0


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="dépôt Git indisponible")
class TestGitignore:
    """Fichiers qui ne doivent jamais être versés au dépôt."""

    @pytest.mark.parametrize("path", [
        ".env", ".env.production", ".env.local", "orders.db", "tecpap.db", "clients_backup.db", "orders.db-wal",
        "orders.db-shm", "orders.db-journal", "evaluation/results/x.db", "backups/backup_1.db.gz", "copie.db.gz",
        "orders_output.json", "data/orders.db",
    ])
    def test_secrets_and_data_are_ignored(self, path):
        assert _is_ignored_by_git(path)

    @pytest.mark.parametrize("path", [".env.example", "backups/.gitkeep", "attachments/.gitkeep", "pyproject.toml"])
    def test_versioned_files_are_not_ignored(self, path):
        assert not _is_ignored_by_git(path)
