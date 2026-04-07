#!/usr/bin/env python3
"""
GitOps Migration Script
Splits k8s-apps monorepo into individual gitops.* repos under cmoreira-dev org.

Final folder structure per repo:
    gitops.<name>/
    ├── helm/
    │   └── <app>/
    │       ├── Chart.yaml
    │       ├── values.yaml
    │       ├── templates/    (if present in source)
    │       └── .app.yaml
    ├── kustomize/
    │   └── <app>/
    │       ├── kustomization.yaml
    │       ├── *.yaml
    │       └── .app.yaml
    ├── terraform/            (empty, ready for future use)
    └── catalog-info.yaml

Usage:
    cd /mnt/c/Users/CassioMoreira/Projects/cmoreira/k8s-apps
    pip install pyyaml
    python3 migrate_gitops.py

Requirements:
    - SSH configured for github.com  →  ssh -T git@github.com
    - gh CLI authenticated           →  gh auth login
    - Run from inside the k8s-apps repo root
"""

import shutil
import subprocess
import sys
import tempfile
import time
import yaml
from pathlib import Path

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

ORG           = "cmoreira-dev"
PERSONAL      = "cassioliveiram"
TEMPLATE_REPO = f"{ORG}/gitops.template"
SOURCE_REPO   = f"{PERSONAL}/k8s-apps"
SOURCE_ROOT   = Path.cwd()  # must be run from k8s-apps root

# Repo definitions: repo_name → list of (source_path, namespace, app_type)
# app_type: "helm" | "kustomize"
REPOS = {
    "gitops.grafana": [
        ("apps/grafana-operator", "monitoring", "helm"),
        ("apps/grafana-mcp",      "monitoring", "helm"),
        ("apps/grafana-server",   "monitoring", "kustomize"),
    ],
    "gitops.observability": [
        ("apps/mimir",                      "monitoring", "helm"),
        ("apps/prometheus",                 "monitoring", "helm"),
        ("apps/pyroscope",                  "monitoring", "helm"),
        ("apps/tempo",                      "monitoring", "helm"),
        ("apps/opentelemetry-operator",     "monitoring", "helm"),
        ("apps/opentelemetry-collector",    "monitoring", "kustomize"),
    ],
    "gitops.istio": [
        ("apps/istio-system", "istio-system", "helm"),
        ("apps/istio-config", "istio-system", "kustomize"),
    ],
    "gitops.alloy": [
        ("apps/alloy-operator", "monitoring", "helm"),
        ("alloy",               "monitoring", "kustomize"),
    ],
    "gitops.cert-manager": [
        ("apps/cert-manager", "cert-manager", "helm"),
    ],
    "gitops.cnpg": [
        ("apps/cnpg-system", "cnpg-system", "helm"),
        ("apps/pg-clusters", "cnpg-system", "kustomize"),
    ],
    "gitops.external-secrets": [
        ("apps/external-secrets", "external-secrets", "helm"),
    ],
    "gitops.headlamp": [
        ("apps/headlamp", "headlamp", "helm"),
    ],
    "gitops.cloudflared": [
        ("apps/cloudflared", "cloudflared", "kustomize"),
    ],
    "gitops.echoserver": [
        ("apps/echoserver", "default", "kustomize"),
    ],
}

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def run(cmd, cwd=None, check=True):
    print(f"  $ {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  ✗ STDERR: {result.stderr.strip()}")
        print(f"  ✗ STDOUT: {result.stdout.strip()}")
        sys.exit(1)
    if result.stdout.strip():
        print(f"    {result.stdout.strip()}")
    return result


def repo_exists(repo_full_name: str) -> bool:
    result = run(f"gh repo view {repo_full_name}", check=False)
    return result.returncode == 0


def get_wave(app_name: str) -> str:
    """Assign ArgoCD sync wave based on app dependencies."""
    waves = {
        # Wave 1 — infra foundations
        "cert-manager":            "1",
        "external-secrets":        "1",
        "cnpg-system":             "1",
        "istio-system":            "1",
        # Wave 2 — operators
        "grafana-operator":        "2",
        "opentelemetry-operator":  "2",
        "alloy-operator":          "2",
        "istio-config":            "2",
        # Wave 3 — observability backends
        "mimir":                   "3",
        "prometheus":              "3",
        "pyroscope":               "3",
        "tempo":                   "3",
        # Wave 4 — collectors & frontends
        "opentelemetry-collector": "4",
        "grafana-mcp":             "4",
        "grafana-server":          "4",
        "alloy":                   "4",
        # Wave 5 — apps
        "headlamp":                "5",
        "cloudflared":             "5",
        "echoserver":              "5",
        "pg-clusters":             "5",
    }
    return waves.get(app_name, "5")


def create_app_yaml(dest_app_path: Path, app_name: str, namespace: str, app_type: str):
    """
    Create .app.yaml inside the app folder.
    Discovered by ApplicationSet via:
      helm/*/.app.yaml
      kustomize/*/.app.yaml
    """
    data = {
        "name":      app_name,
        "namespace": namespace,
        "type":      app_type,
        "wave":      get_wave(app_name),
    }
    with open(dest_app_path / ".app.yaml", "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=True)
    print(f"    ✓ .app.yaml  namespace={namespace}  wave={data['wave']}")


def migrate_helm_app(source_path: Path, clone_path: Path, app_name: str):
    """
    Copies a helm wrapper-chart app into clone_path/helm/<app_name>/

    Source (k8s-apps/apps/<app>/):
        Chart.yaml        ← real dependency name / version / repository
        values.yaml       ← real Helm values
        templates/        ← optional custom resources
        README.md         ← optional

    Destination (gitops.<repo>/helm/<app>/):
        Chart.yaml        ← reconstructed with real dependency values
        values.yaml       ← copied from source
        templates/        ← copied from source (if present)
        .app.yaml         ← generated
    """
    dest_app = clone_path / "helm" / app_name
    dest_app.mkdir(parents=True, exist_ok=True)

    # Read source Chart.yaml
    source_chart_path = source_path / "Chart.yaml"
    if not source_chart_path.exists():
        print(f"    ✗ Chart.yaml not found in {source_path}")
        sys.exit(1)

    with open(source_chart_path) as f:
        source_chart = yaml.safe_load(f)

    deps = source_chart.get("dependencies", [])

    if not deps:
        shutil.copy2(source_chart_path, dest_app / "Chart.yaml")
        print(f"    ✓ Chart.yaml copied as-is (no dependencies found)")
    else:
        dep         = deps[0]  # wrapper charts have exactly one dependency
        dep_name    = dep.get("name", app_name)
        dep_version = dep.get("version", "*")
        dep_repo    = dep.get("repository", "")

        chart_content = (
            f"apiVersion: v2\n"
            f"name: {app_name}-wrapper\n"
            f"description: Wrapper chart to install {dep_name} via Helm\n"
            f"type: application\n"
            f"version: 0.1.0\n"
            f"\n"
            f"dependencies:\n"
            f"  - name: {dep_name}\n"
            f'    version: "{dep_version}"\n'
            f"    repository: {dep_repo}\n"
        )
        (dest_app / "Chart.yaml").write_text(chart_content)
        print(f"    ✓ Chart.yaml  dep={dep_name}  version={dep_version}")

    # Copy values.yaml
    source_values = source_path / "values.yaml"
    if source_values.exists():
        shutil.copy2(source_values, dest_app / "values.yaml")
        print(f"    ✓ values.yaml copied")
    else:
        dep_name = deps[0].get("name", app_name) if deps else app_name
        (dest_app / "values.yaml").write_text(
            f"# {dep_name}:\n"
            f"  # Set any values for {app_name} here\n"
        )
        print(f"    ✓ values.yaml created (empty)")

    # Copy templates/ if present
    source_templates = source_path / "templates"
    if source_templates.exists():
        shutil.copytree(source_templates, dest_app / "templates", dirs_exist_ok=True)
        count = len(list(source_templates.rglob("*")))
        print(f"    ✓ templates/ copied ({count} files)")

    # Copy README.md if present
    source_readme = source_path / "README.md"
    if source_readme.exists():
        shutil.copy2(source_readme, dest_app / "README.md")
        print(f"    ✓ README.md copied")

    return dest_app


def migrate_kustomize_app(source_path: Path, clone_path: Path, app_name: str):
    """
    Copies a kustomize app into clone_path/kustomize/<app_name>/

    Source (k8s-apps/apps/<app>/  or  k8s-apps/alloy/):
        kustomization.yaml
        *.yaml

    Destination (gitops.<repo>/kustomize/<app>/):
        kustomization.yaml
        *.yaml
        .app.yaml
    """
    dest_app = clone_path / "kustomize" / app_name
    dest_app.mkdir(parents=True, exist_ok=True)

    for item in source_path.iterdir():
        dest = dest_app / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)
    print(f"    ✓ kustomize files copied")

    return dest_app


def scaffold_terraform_dir(clone_path: Path):
    """Create empty terraform/ folder with a .gitkeep so it's tracked by git."""
    tf_dir = clone_path / "terraform"
    tf_dir.mkdir(exist_ok=True)
    (tf_dir / ".gitkeep").touch()
    print(f"  ✓ terraform/ scaffolded (ready for future use)")


def patch_catalog_info(repo_path: Path, repo_name: str):
    """Replace template placeholders in catalog-info.yaml."""
    catalog = repo_path / "catalog-info.yaml"
    if not catalog.exists():
        return
    content = catalog.read_text()
    content = content.replace("gitops.template", repo_name)
    content = content.replace("gitops-template", repo_name)
    catalog.write_text(content)
    print(f"  ✓ catalog-info.yaml patched")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    print("=" * 60)
    print("GitOps Migration Script")
    print("=" * 60)

    if not (SOURCE_ROOT / "apps").exists():
        print("✗ ERROR: Run this script from the k8s-apps repo root!")
        sys.exit(1)

    run("gh auth status")
    result = run("ssh -T git@github.com", check=False)
    if "Hi " not in result.stderr:
        print("✗ SSH auth to github.com failed — check your SSH key setup")
        sys.exit(1)

    deleted_paths = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        for repo_name, apps in REPOS.items():
            repo_full = f"{ORG}/{repo_name}"
            print(f"\n{'─'*60}")
            print(f"📦  {repo_full}")
            print(f"{'─'*60}")

            # 1. Create repo from template (idempotent)
            if repo_exists(repo_full):
                print(f"  ⚠ Repo already exists — skipping creation")
            else:
                run(f"gh repo create {repo_full} --template={TEMPLATE_REPO} --private")
                print(f"  ✓ Repo created from template")
                time.sleep(3)  # wait for GitHub to finish async template init

            # 2. Clone
            clone_path = tmp / repo_name
            run(f"git clone git@github.com:{repo_full}.git {clone_path}")

            # 3. Scaffold terraform/ dir
            scaffold_terraform_dir(clone_path)

            # 4. Migrate each app
            for source_rel, namespace, app_type in apps:
                source_path = SOURCE_ROOT / source_rel
                app_name    = Path(source_rel).name

                print(f"\n  → {app_name}  [{app_type}]")

                if not source_path.exists():
                    print(f"    ⚠ Source path not found ({source_path}) — skipping")
                    continue

                if app_type == "helm":
                    dest_app = migrate_helm_app(source_path, clone_path, app_name)
                else:
                    dest_app = migrate_kustomize_app(source_path, clone_path, app_name)

                create_app_yaml(dest_app, app_name, namespace, app_type)
                deleted_paths.append(source_rel)

            # 5. Patch catalog-info.yaml
            patch_catalog_info(clone_path, repo_name)

            # 6. Commit + push
            run("git add -A", cwd=clone_path)
            commit_result = run(
                'git commit -m "feat: migrate apps from k8s-apps monorepo"',
                cwd=clone_path,
                check=False
            )
            if commit_result.returncode != 0:
                if "nothing to commit" in commit_result.stdout:
                    print(f"  ✓ Nothing new to commit — already up to date")
                else:
                    print(f"  ✗ Commit failed: {commit_result.stderr.strip()}")
                    sys.exit(1)
            else:
                # Pull --rebase first in case the template created an initial commit
                # that wasn't present when we cloned (GitHub template repos async init)
                run("git pull --rebase origin main", cwd=clone_path)
                run("git push origin main", cwd=clone_path)
                print(f"\n  ✓ Pushed → {repo_full}/main")

    # ─────────────────────────────────────────
    # Open deletion PR on source repo
    # ─────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"🗑   Opening deletion PR on {SOURCE_REPO}")
    print(f"{'─'*60}")

    branch = "chore/remove-migrated-apps"
    run(f"git checkout -b {branch}", cwd=SOURCE_ROOT)

    for path_rel in deleted_paths:
        path_abs = SOURCE_ROOT / path_rel
        if path_abs.exists():
            if path_abs.is_dir():
                shutil.rmtree(path_abs)
            else:
                path_abs.unlink()
            print(f"  ✓ Deleted {path_rel}")

    run("git add -A", cwd=SOURCE_ROOT)
    run(
        'git commit -m "chore: remove apps migrated to gitops.* repos\n\n'
        'Apps migrated to individual repos under cmoreira-dev org.\n'
        'DO NOT MERGE before updating ArgoCD ApplicationSet."',
        cwd=SOURCE_ROOT,
    )
    run(f"git push origin {branch}", cwd=SOURCE_ROOT)

    migrated_list = "\n".join(
        f"- [{ORG}/{r}](https://github.com/{ORG}/{r})" for r in REPOS
    )
    pr_body = (
        "## Remove apps migrated to individual gitops.* repos\n\n"
        f"Apps have been migrated to dedicated repositories under `{ORG}`.\n\n"
        f"### Migrated repos\n{migrated_list}\n\n"
        "## Before merging\n\n"
        "1. `kubectl apply -f applicationset.yaml -n argocd`\n"
        "2. Verify **all apps are healthy** in ArgoCD\n"
        "3. Update ArgoCD AppProject if needed\n"
        "4. Only then merge — avoids orphaning live ArgoCD Applications\n"
    )

    run(
        f"gh pr create "
        f"--repo {SOURCE_REPO} "
        f"--base main "
        f"--head {branch} "
        f'--title "chore: remove apps migrated to gitops.* repos" '
        f'--body \'{pr_body}\'',
        cwd=SOURCE_ROOT,
    )

    print(f"\n{'='*60}")
    print("✅  Migration complete!")
    print(f"{'='*60}")
    print("\nNext steps:")
    print("  1. kubectl apply -f applicationset.yaml -n argocd")
    print("  2. Verify all apps healthy in ArgoCD UI")
    print("  3. Merge the PR on k8s-apps")


if __name__ == "__main__":
    main()
