# Infra relocation plan (VM-build-gated)

Physical moves for Phase 5's target layout (`infra/{docker,aws,gcp,packer,systemd,ansible}`).
**Do not run on a machine where you cannot rebuild the golden image.**
The application pipeline is unaffected either way — these are deploy-asset
paths only — but CI deploy workflows reference them, so every reference is
rewritten in the same commit.

## 0. Preconditions

```bash
git checkout refactor/jobbots-phase2-6   # or main after merge
jobbots infra --audit                    # must be OK before starting
```

## 1. Moves (git preserves history)

```bash
mkdir -p infra/{docker,aws,gcp,packer,systemd,ansible}
git mv docker/* infra/docker/ && git mv Dockerfile.bot docker-compose.yml docker-compose.local.yml docker-setup.ps1 infra/docker/
git mv terraform/persistent infra/aws/terraform && git mv terraform/main.tf terraform/outputs.tf terraform/variables.tf terraform/vm_setup.ps1 terraform/cloud-init.yaml.tftpl terraform/*.tfvars.example infra/aws/terraform/
git mv terraform/gcp infra/gcp/terraform
git mv packer/* infra/packer/                       # includes linux/ + scripts/
git mv infra/packer/linux/systemd infra/systemd/systemd  # canonical systemd home
git mv ansible/* infra/ansible/
```

## 2. Reference rewrites (exact, greppable)

```bash
# CI workflows + scripts: path prefixes
git grep -l 'terraform/persistent' | xargs sed -i '' 's|terraform/persistent|infra/aws/terraform|g'
git grep -l 'terraform/gcp'        | xargs sed -i '' 's|terraform/gcp|infra/gcp/terraform|g'
git grep -l 'packer/linux'         | xargs sed -i '' 's|packer/linux|infra/packer/linux|g'
git grep -l 'packer/scripts'       | xargs sed -i '' 's|packer/scripts|infra/packer/scripts|g'
git grep -l 'packer/jobbots'       | xargs sed -i '' 's|packer/jobbots|infra/packer/jobbots|g'
git grep -l 'docker/Dockerfile'    | xargs sed -i '' 's|docker/Dockerfile|infra/docker/Dockerfile|g'
git grep -l 'ansible/playbook'     | xargs sed -i '' 's|ansible/playbook|infra/ansible/playbook|g'
# compose files: context moves two levels up
sed -i '' 's|context: \.$|context: ../..|' infra/docker/docker-compose.local.yml
# ci.yml path filters + compose invocation
sed -i '' "s|'docker/\*\*'|'infra/docker/**'|; s|'docker-compose.yml'|'infra/docker/docker-compose.yml'|" .github/workflows/ci.yml
# packer HCL self-references (file sources are relative to the template dir)
grep -rn '"\.\./' infra/packer/*.pkr.hcl   # review each; adjust to new depth
```

## 3. Verify locally (no cloud calls)

```bash
jobbots infra --audit          # update jobbots/app/infra.py paths first, then OK
bash -n scripts/*.sh deploy*.sh vmctl
python -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]"
docker compose -f infra/docker/docker-compose.local.yml config --quiet
packer fmt -check infra/packer/   # optional
pytest automation_monorepo/tests -q --tb=no -rf   # zero NEW failures vs baseline
jobbots doctor --quick && jobbots qa check        # 43/43
```

## 4. The real gate (only you can run this)

```bash
scripts/build_gcp_golden.sh        # or the AWS packer build — full image build
# boot a fresh VM from the new image, then on it:
cd /opt/jobbots/app && jobbots doctor --quick && jobbots qa check && jobbots status
```

If the image build or boot checks fail: `git revert` the relocation commit —
old paths are the only thing that changed.

## 5. Register the new paths

Update `INFRA_MODULES` in `jobbots/app/infra.py` (paths now start with
`infra/…`) — `jobbots infra --audit` and the `test_infra_layout.py` suite
must stay green.
