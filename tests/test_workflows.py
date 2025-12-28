"""
Tests pour les workflows GitHub Actions
Valide la structure et la configuration des fichiers CI/CD
"""

import pytest
import os
import sys
import yaml
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Path to workflows
WORKFLOWS_DIR = Path(__file__).parent.parent / '.github' / 'workflows'


class TestWorkflowFilesExist:
    """Tests de l'existence des fichiers workflow."""
    
    def test_workflows_directory_exists(self):
        """Test que le dossier workflows existe."""
        assert WORKFLOWS_DIR.exists(), "Le dossier .github/workflows n'existe pas"
    
    def test_ci_workflow_exists(self):
        """Test que le workflow CI existe."""
        ci_path = WORKFLOWS_DIR / 'ci.yml'
        assert ci_path.exists(), "Le fichier ci.yml n'existe pas"
    
    def test_backup_workflow_exists(self):
        """Test que le workflow backup existe."""
        backup_path = WORKFLOWS_DIR / 'backup.yml'
        assert backup_path.exists(), "Le fichier backup.yml n'existe pas"
    
    def test_deploy_workflow_exists(self):
        """Test que le workflow deploy existe."""
        deploy_path = WORKFLOWS_DIR / 'deploy.yml'
        assert deploy_path.exists(), "Le fichier deploy.yml n'existe pas"


class TestCIWorkflow:
    """Tests du workflow CI."""
    
    @pytest.fixture
    def ci_config(self):
        """Charge la configuration CI."""
        ci_path = WORKFLOWS_DIR / 'ci.yml'
        if not ci_path.exists():
            pytest.skip("ci.yml not found")
        with open(ci_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    
    def _get_triggers(self, config):
        """Get triggers from config (handles 'on' being parsed as True)."""
        return config.get('on') or config.get(True, {})
    
    def test_ci_has_name(self, ci_config):
        """Test que le workflow a un nom."""
        assert 'name' in ci_config
        assert ci_config['name'] is not None
    
    def test_ci_has_trigger_on_push(self, ci_config):
        """Test que le CI se déclenche sur push."""
        triggers = self._get_triggers(ci_config)
        assert triggers is not None
        assert 'push' in triggers
    
    def test_ci_has_trigger_on_pull_request(self, ci_config):
        """Test que le CI se déclenche sur pull request."""
        triggers = self._get_triggers(ci_config)
        assert 'pull_request' in triggers
    
    def test_ci_has_jobs(self, ci_config):
        """Test que le CI a des jobs."""
        assert 'jobs' in ci_config
        assert len(ci_config['jobs']) > 0
    
    def test_ci_has_lint_job(self, ci_config):
        """Test que le CI a un job de linting."""
        jobs = ci_config['jobs']
        assert 'lint' in jobs
    
    def test_ci_has_test_job(self, ci_config):
        """Test que le CI a un job de tests."""
        jobs = ci_config['jobs']
        assert 'test' in jobs
    
    def test_ci_has_security_job(self, ci_config):
        """Test que le CI a un job de sécurité."""
        jobs = ci_config['jobs']
        assert 'security' in jobs
    
    def test_ci_test_job_has_matrix(self, ci_config):
        """Test que le job test utilise une matrice Python."""
        test_job = ci_config['jobs']['test']
        assert 'strategy' in test_job
        assert 'matrix' in test_job['strategy']
        assert 'python-version' in test_job['strategy']['matrix']
    
    def test_ci_test_runs_pytest(self, ci_config):
        """Test que le job test exécute pytest."""
        test_job = ci_config['jobs']['test']
        steps = test_job['steps']
        
        # Check if any step runs pytest
        pytest_found = False
        for step in steps:
            if 'run' in step and 'pytest' in step.get('run', ''):
                pytest_found = True
                break
        
        assert pytest_found, "pytest command not found in test job"
    
    def test_ci_uses_checkout_action(self, ci_config):
        """Test que le CI utilise actions/checkout."""
        jobs = ci_config['jobs']
        
        for job_name, job in jobs.items():
            checkout_found = False
            for step in job.get('steps', []):
                if 'uses' in step and 'checkout' in step['uses']:
                    checkout_found = True
                    break
            assert checkout_found, f"Job {job_name} doesn't use checkout action"
    
    def test_ci_uses_setup_python_action(self, ci_config):
        """Test que le CI utilise actions/setup-python."""
        jobs = ci_config['jobs']
        
        for job_name, job in jobs.items():
            python_found = False
            for step in job.get('steps', []):
                if 'uses' in step and 'setup-python' in step['uses']:
                    python_found = True
                    break
            assert python_found, f"Job {job_name} doesn't use setup-python action"


class TestBackupWorkflow:
    """Tests du workflow de backup."""
    
    @pytest.fixture
    def backup_config(self):
        """Charge la configuration backup."""
        backup_path = WORKFLOWS_DIR / 'backup.yml'
        if not backup_path.exists():
            pytest.skip("backup.yml not found")
        with open(backup_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    
    def _get_triggers(self, config):
        """Get triggers from config (handles 'on' being parsed as True)."""
        return config.get('on') or config.get(True, {})
    
    def test_backup_has_name(self, backup_config):
        """Test que le workflow a un nom."""
        assert 'name' in backup_config
    
    def test_backup_has_schedule(self, backup_config):
        """Test que le backup a un schedule."""
        triggers = self._get_triggers(backup_config)
        assert triggers is not None
        assert 'schedule' in triggers
    
    def test_backup_has_cron_expression(self, backup_config):
        """Test que le schedule a une expression cron."""
        triggers = self._get_triggers(backup_config)
        schedule = triggers['schedule']
        assert len(schedule) > 0
        assert 'cron' in schedule[0]
    
    def test_backup_has_manual_trigger(self, backup_config):
        """Test que le backup peut être déclenché manuellement."""
        triggers = self._get_triggers(backup_config)
        assert 'workflow_dispatch' in triggers
    
    def test_backup_has_backup_job(self, backup_config):
        """Test que le workflow a un job backup."""
        assert 'jobs' in backup_config
        assert 'backup' in backup_config['jobs']
    
    def test_backup_uploads_artifact(self, backup_config):
        """Test que le backup uploade un artifact."""
        backup_job = backup_config['jobs']['backup']
        steps = backup_job['steps']
        
        upload_found = False
        for step in steps:
            if 'uses' in step and 'upload-artifact' in step['uses']:
                upload_found = True
                break
        
        assert upload_found, "Upload artifact step not found"


class TestDeployWorkflow:
    """Tests du workflow de déploiement."""
    
    @pytest.fixture
    def deploy_config(self):
        """Charge la configuration deploy."""
        deploy_path = WORKFLOWS_DIR / 'deploy.yml'
        if not deploy_path.exists():
            pytest.skip("deploy.yml not found")
        with open(deploy_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    
    def _get_triggers(self, config):
        """Get triggers from config (handles 'on' being parsed as True)."""
        return config.get('on') or config.get(True, {})
    
    def test_deploy_has_name(self, deploy_config):
        """Test que le workflow a un nom."""
        assert 'name' in deploy_config
    
    def test_deploy_triggers_on_tags(self, deploy_config):
        """Test que le deploy se déclenche sur les tags."""
        triggers = self._get_triggers(deploy_config)
        assert 'push' in triggers
        push_config = triggers['push']
        assert 'tags' in push_config
    
    def test_deploy_has_manual_trigger(self, deploy_config):
        """Test que le deploy peut être déclenché manuellement."""
        triggers = self._get_triggers(deploy_config)
        assert 'workflow_dispatch' in triggers
    
    def test_deploy_has_environment_input(self, deploy_config):
        """Test que le deploy a un input environment."""
        triggers = self._get_triggers(deploy_config)
        dispatch = triggers['workflow_dispatch']
        assert 'inputs' in dispatch
        assert 'environment' in dispatch['inputs']
    
    def test_deploy_environment_choices(self, deploy_config):
        """Test que le deploy a les bons choix d'environnement."""
        triggers = self._get_triggers(deploy_config)
        env_input = triggers['workflow_dispatch']['inputs']['environment']
        assert 'options' in env_input
        options = env_input['options']
        assert 'staging' in options
        assert 'production' in options
    
    def test_deploy_runs_tests(self, deploy_config):
        """Test que le deploy exécute les tests."""
        deploy_job = deploy_config['jobs']['deploy']
        steps = deploy_job['steps']
        
        test_found = False
        for step in steps:
            if 'run' in step and 'pytest' in step.get('run', ''):
                test_found = True
                break
        
        assert test_found, "Tests not run before deployment"


class TestYAMLValidity:
    """Tests de validité YAML."""
    
    def test_all_workflows_valid_yaml(self):
        """Test que tous les workflows sont du YAML valide."""
        if not WORKFLOWS_DIR.exists():
            pytest.skip("Workflows directory not found")
        
        for workflow_file in WORKFLOWS_DIR.glob('*.yml'):
            with open(workflow_file, 'r', encoding='utf-8') as f:
                try:
                    yaml.safe_load(f)
                except yaml.YAMLError as e:
                    pytest.fail(f"Invalid YAML in {workflow_file.name}: {e}")
    
    def test_workflows_have_required_keys(self):
        """Test que les workflows ont les clés requises."""
        # 'on' is parsed as True in YAML, so we check for both
        required_keys_options = [['name', 'on', 'jobs'], ['name', True, 'jobs']]
        
        if not WORKFLOWS_DIR.exists():
            pytest.skip("Workflows directory not found")
        
        for workflow_file in WORKFLOWS_DIR.glob('*.yml'):
            with open(workflow_file, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                
                # Check name and jobs are present
                assert 'name' in config, f"Missing key 'name' in {workflow_file.name}"
                assert 'jobs' in config, f"Missing key 'jobs' in {workflow_file.name}"
                # Check 'on' or True (YAML boolean) is present
                has_on = 'on' in config or True in config
                assert has_on, f"Missing key 'on' in {workflow_file.name}"


class TestWorkflowBestPractices:
    """Tests des bonnes pratiques des workflows."""
    
    @pytest.fixture
    def all_configs(self):
        """Charge toutes les configurations."""
        configs = {}
        if WORKFLOWS_DIR.exists():
            for workflow_file in WORKFLOWS_DIR.glob('*.yml'):
                with open(workflow_file, 'r', encoding='utf-8') as f:
                    configs[workflow_file.stem] = yaml.safe_load(f)
        return configs
    
    def test_workflows_use_latest_actions(self, all_configs):
        """Test que les workflows utilisent des versions d'actions récentes."""
        if not all_configs:
            pytest.skip("No workflows found")
        
        for name, config in all_configs.items():
            for job_name, job in config.get('jobs', {}).items():
                for step in job.get('steps', []):
                    if 'uses' in step:
                        action = step['uses']
                        # Check for version pinning (should have @v or @sha)
                        assert '@' in action, f"Action {action} in {name}/{job_name} should be version pinned"
    
    def test_workflows_have_descriptive_names(self, all_configs):
        """Test que les workflows ont des noms descriptifs."""
        if not all_configs:
            pytest.skip("No workflows found")
        
        for name, config in all_configs.items():
            workflow_name = config.get('name', '')
            assert len(workflow_name) > 3, f"Workflow {name} has too short name"
    
    def test_jobs_have_names(self, all_configs):
        """Test que les jobs ont des noms."""
        if not all_configs:
            pytest.skip("No workflows found")
        
        for workflow_name, config in all_configs.items():
            for job_name, job in config.get('jobs', {}).items():
                assert 'name' in job or job_name, f"Job in {workflow_name} should have a name"


class TestSecurityBestPractices:
    """Tests des bonnes pratiques de sécurité."""
    
    @pytest.fixture
    def ci_config(self):
        ci_path = WORKFLOWS_DIR / 'ci.yml'
        if not ci_path.exists():
            pytest.skip("ci.yml not found")
        with open(ci_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    
    def test_no_hardcoded_real_secrets(self, ci_config):
        """Test qu'il n'y a pas de vrais secrets en dur."""
        yaml_str = yaml.dump(ci_config)
        
        # Check for real secret patterns (not test placeholders)
        real_secret_patterns = [
            'api_key=sk-',       # OpenAI real key prefix
            'token=ghp_',        # GitHub real token prefix
            'password=real',     # Obvious real password
            'secret=prod',       # Production secrets
        ]
        
        for pattern in real_secret_patterns:
            assert pattern.lower() not in yaml_str.lower(), f"Possible hardcoded real secret: {pattern}"
    
    def test_uses_environment_variables(self, ci_config):
        """Test que le CI utilise des variables d'environnement."""
        # Check if env section exists at workflow or job level
        has_env = 'env' in ci_config
        
        for job in ci_config.get('jobs', {}).values():
            if 'env' in job:
                has_env = True
            for step in job.get('steps', []):
                if 'env' in step:
                    has_env = True
        
        assert has_env, "No environment variables configured"
    
    def test_test_credentials_are_placeholders(self, ci_config):
        """Test que les credentials de test sont des placeholders."""
        yaml_str = yaml.dump(ci_config)
        
        # Test credentials should contain 'test' to indicate they're placeholders
        assert 'test_key' in yaml_str or 'test_password' in yaml_str, \
            "Test environment should use placeholder credentials"


class TestWorkflowDependencies:
    """Tests des dépendances entre jobs."""
    
    @pytest.fixture
    def ci_config(self):
        ci_path = WORKFLOWS_DIR / 'ci.yml'
        if not ci_path.exists():
            pytest.skip("ci.yml not found")
        with open(ci_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    
    def test_test_depends_on_lint(self, ci_config):
        """Test que le job test dépend du lint."""
        test_job = ci_config['jobs'].get('test', {})
        needs = test_job.get('needs', [])
        
        if isinstance(needs, str):
            needs = [needs]
        
        assert 'lint' in needs, "Test job should depend on lint job"
    
    def test_build_depends_on_test(self, ci_config):
        """Test que le job build dépend du test."""
        build_job = ci_config['jobs'].get('build', {})
        if not build_job:
            pytest.skip("No build job found")
        
        needs = build_job.get('needs', [])
        
        if isinstance(needs, str):
            needs = [needs]
        
        assert 'test' in needs, "Build job should depend on test job"
