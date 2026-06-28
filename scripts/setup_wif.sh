#!/usr/bin/env bash
# setup_wif.sh — one-time, IDEMPOTENT setup of keyless GitHub Actions → GCP auth
# for the schedule-portal Cloud Run deploy workflow (Workload Identity Federation,
# OIDC; NO service-account JSON key is ever created or exported).
#
# ────────────────────────────────────────────────────────────────────────────
#  DO NOT run this blindly. It MUTATES GCP IAM (creates a WIF pool/provider + a
#  deploy service account + role bindings). It is safe to re-run (every step is
#  create-or-skip / additive), but it is NOT a read-only script. Run it once,
#  intentionally, when wiring up CI for the first time. — Sebastian's go.
# ────────────────────────────────────────────────────────────────────────────
#
# What it provisions (exact names — referenced verbatim by .github/workflows/deploy.yml):
#
#   Project              : giready-portal           (number 417139937755)
#   WIF pool             : github-actions
#   WIF provider         : schedule-portal-backend
#   Deploy SA            : gha-deploy-schedule@giready-portal.iam.gserviceaccount.com
#
#   Full provider resource name (the workflow's workload_identity_provider):
#     projects/417139937755/locations/global/workloadIdentityPools/github-actions/providers/schedule-portal-backend
#
# Trust is attribute-restricted so ONLY this repo's main branch can mint a token:
#     attribute.repository == "plaiful67/schedule-portal-backend"
#   AND
#     attribute.ref        == "refs/heads/main"
# (Both enforced in the provider's --attribute-condition. The repo→SA binding is
#  further scoped to attribute.repository, so even another repo in the same pool
#  could not impersonate this SA.)
#
# Least-privilege roles bound to the deploy SA (giready-portal project):
#     roles/run.admin                  — deploy revisions + migrate traffic
#     roles/iam.serviceAccountUser     — act-as the Cloud Run runtime SA
#     roles/artifactregistry.writer    — push the built image
#     roles/cloudbuild.builds.editor   — `gcloud run deploy --source .` uses Cloud Build
#     roles/storage.admin              — Cloud Build staging bucket (source upload)
#
# The deploy SA must also be allowed to act-as the Cloud Run RUNTIME service
# account (the default compute SA the service currently runs as). That binding
# is created below as well.
#
# Verify after running (read-only):
#   gcloud iam workload-identity-pools providers describe schedule-portal-backend \
#     --location=global --workload-identity-pool=github-actions \
#     --project=giready-portal --format='yaml(attributeCondition,attributeMapping,oidc)'
#   gcloud projects get-iam-policy giready-portal \
#     --flatten='bindings[].members' \
#     --filter='bindings.members:gha-deploy-schedule@giready-portal.iam.gserviceaccount.com' \
#     --format='table(bindings.role)'
#
# Usage:
#   bash scripts/setup_wif.sh

set -euo pipefail

# ── Config (do not change without updating deploy.yml to match) ───────────────
PROJECT_ID="giready-portal"
PROJECT_NUMBER="417139937755"
POOL_ID="github-actions"
PROVIDER_ID="schedule-portal-backend"
SA_NAME="gha-deploy-schedule"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
GITHUB_REPO="plaiful67/schedule-portal-backend"
GITHUB_REF="refs/heads/main"
REGION="us-central1"   # Cloud Run + Artifact Registry region (must match deploy.yml)

# The Cloud Run RUNTIME service account the service executes as (read off the
# live service: 417139937755-compute@developer.gserviceaccount.com). The deploy
# SA needs actAs on THIS account to deploy a revision that runs as it.
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

ISSUER_URI="https://token.actions.githubusercontent.com"

echo "→ Project:        ${PROJECT_ID} (${PROJECT_NUMBER})"
echo "→ WIF pool:       ${POOL_ID}"
echo "→ WIF provider:   ${PROVIDER_ID}"
echo "→ Deploy SA:      ${SA_EMAIL}"
echo "→ Trusts:         repo=${GITHUB_REPO} ref=${GITHUB_REF}"
echo

# ── 0. Enable the APIs the flow needs (idempotent) ───────────────────────────
echo "→ Enabling required APIs (idempotent) …"
gcloud services enable \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  --project="${PROJECT_ID}"

# ── 0b. Artifact Registry repo for `gcloud run deploy --source` (create-or-skip) ─
# --source builds push images here. Pre-creating it lets the deploy SA stay at
# artifactregistry.writer (it does NOT need repositories.create / admin).
if gcloud artifacts repositories describe cloud-run-source-deploy \
     --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "= AR repo cloud-run-source-deploy already exists"
else
  echo "+ creating AR repo cloud-run-source-deploy (${REGION})"
  gcloud artifacts repositories create cloud-run-source-deploy \
    --repository-format=docker --location="${REGION}" --project="${PROJECT_ID}"
fi

# ── 1. Deploy service account (create-or-skip) ───────────────────────────────
if gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "= deploy SA ${SA_EMAIL} already exists"
else
  echo "+ creating deploy SA ${SA_EMAIL}"
  gcloud iam service-accounts create "${SA_NAME}" \
    --project="${PROJECT_ID}" \
    --display-name="GitHub Actions deploy (schedule-portal, keyless WIF)"
  # A freshly-created SA is not immediately usable in IAM bindings (propagation
  # lag) — wait until it is describable so the role bindings below don't fail
  # with 'does not exist' (which previously forced a manual re-run).
  echo "  waiting for the SA to propagate…"
  for _ in $(seq 1 30); do
    gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" >/dev/null 2>&1 && break
    sleep 2
  done
fi

# ── 2. Least-privilege project roles on the deploy SA (idempotent add) ────────
for ROLE in \
  roles/run.admin \
  roles/iam.serviceAccountUser \
  roles/artifactregistry.writer \
  roles/cloudbuild.builds.editor \
  roles/storage.admin
do
  echo "→ binding ${ROLE} → ${SA_EMAIL}"
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${ROLE}" \
    --condition=None \
    --quiet >/dev/null
done

# ── 3. actAs on the Cloud Run runtime SA (so the deploy can set runAs) ────────
echo "→ granting ${SA_EMAIL} actAs on runtime SA ${RUNTIME_SA}"
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" \
  --project="${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/iam.serviceAccountUser" \
  --quiet >/dev/null

# ── 4. Workload Identity Pool (create-or-skip) ───────────────────────────────
if gcloud iam workload-identity-pools describe "${POOL_ID}" \
     --location=global --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "= WIF pool ${POOL_ID} already exists"
else
  echo "+ creating WIF pool ${POOL_ID}"
  gcloud iam workload-identity-pools create "${POOL_ID}" \
    --location=global --project="${PROJECT_ID}" \
    --display-name="GitHub Actions"
fi

# ── 5. OIDC provider (create-or-skip), attribute-restricted to repo + ref ─────
# attribute-condition pins BOTH the repo and the branch: only the main branch
# of plaiful67/schedule-portal-backend can present a usable token.
ATTR_CONDITION="assertion.repository=='${GITHUB_REPO}' && assertion.ref=='${GITHUB_REF}'"

if gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
     --location=global --workload-identity-pool="${POOL_ID}" \
     --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "= WIF provider ${PROVIDER_ID} already exists — updating attribute-condition"
  gcloud iam workload-identity-pools providers update-oidc "${PROVIDER_ID}" \
    --location=global --workload-identity-pool="${POOL_ID}" \
    --project="${PROJECT_ID}" \
    --attribute-condition="${ATTR_CONDITION}"
else
  echo "+ creating WIF OIDC provider ${PROVIDER_ID}"
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
    --location=global --workload-identity-pool="${POOL_ID}" \
    --project="${PROJECT_ID}" \
    --display-name="schedule-portal-backend main" \
    --issuer-uri="${ISSUER_URI}" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="${ATTR_CONDITION}"
fi

# ── 6. Let the repo's identity impersonate the deploy SA (scoped to repo) ─────
# Even though the provider already pins repo+ref, scope the impersonation binding
# to attribute.repository too (defense in depth — a second repo joined to this
# pool still couldn't act as this SA).
WIF_PRINCIPAL="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${GITHUB_REPO}"

echo "→ granting workloadIdentityUser to repo principalSet on ${SA_EMAIL}"
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --project="${PROJECT_ID}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="${WIF_PRINCIPAL}" \
  --quiet >/dev/null

echo
echo "✓ WIF setup complete."
echo
echo "Put these in .github/workflows/deploy.yml (already referenced there):"
echo "  workload_identity_provider:"
echo "    projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"
echo "  service_account: ${SA_EMAIL}"
