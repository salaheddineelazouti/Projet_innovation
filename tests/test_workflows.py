"""Tests des workflows GitHub Actions (structure et contenu des fichiers .github/workflows)."""

from pathlib import Path

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).parent.parent / ".github" / "workflows"


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

    def test_ci_has_test_job(self, ci_config):
        assert "tests" in ci_config["jobs"]

    def test_ci_has_security_job(self, ci_config):
        steps = ci_config["jobs"]["security"]["steps"]
        audits = [step for step in steps if step.get("run", "").startswith(("bandit ", "pip-audit "))]
        assert any(step["run"].startswith("bandit -r") for step in audits)
        assert any(step["run"].startswith("pip-audit") for step in audits)
        # Rapport sans blocage : chaque alerte est examinée avant d'être traitée.
        assert all(step.get("continue-on-error") is True for step in audits)

    def test_ci_test_job_has_matrix(self, ci_config):
        matrix = ci_config["jobs"]["tests"]["strategy"]["matrix"]
        assert matrix["python-version"] == ["3.10", "3.11", "3.12"]

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

    def test_ci_uses_checkout_action(self, ci_config):
        for name, job in ci_config["jobs"].items():
            assert any("actions/checkout" in action for action in _uses(job)), name

    def test_ci_uses_setup_python_action(self, ci_config):
        for name, job in ci_config["jobs"].items():
            assert any("actions/setup-python" in action for action in _uses(job)), name


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

    def test_deploy_builds_and_uploads_image(self, deploy_config):
        image = deploy_config["jobs"]["image"]
        assert "docker build" in _runs(image)
        assert "docker save" in _runs(image)
        assert any("upload-artifact" in action for action in _uses(image))


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

    def test_workflows_use_latest_actions(self, all_configs):
        """Chaque action est épinglée sur une version."""
        for name, config in all_configs.items():
            for job in config["jobs"].values():
                for action in _uses(job):
                    assert "@" in action, f"{action} ({name}) sans version"

    def test_workflows_have_descriptive_names(self, all_configs):
        for name, config in all_configs.items():
            assert len(config.get("name", "")) > 1, name

    def test_jobs_have_names(self, all_configs):
        for name, config in all_configs.items():
            for job_id, job in config["jobs"].items():
                assert job.get("name"), f"{name}/{job_id}"


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
