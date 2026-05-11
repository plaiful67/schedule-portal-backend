#!/usr/bin/env bash
# Deploy schedule-portal-backend to Cloud Run.
#
# Prerequisites (one-time):
#   1. `gcloud auth login`
#   2. `gcloud config set project YOUR-PROJECT-ID`
#   3. Enable APIs:
#        gcloud services enable run.googleapis.com \
#                                cloudbuild.googleapis.com \
#                                artifactregistry.googleapis.com
#
# Usage:  scripts/deploy.sh
#
# Env vars you may want to override (see defaults below):
#   GCP_PROJECT         — gcloud will use the active project if unset
#   GCP_REGION          — defaults to us-central1
#   SERVICE_NAME        — defaults to schedule-portal
#   ALLOWED_ORIGINS     — comma-separated; defaults to the two giready domains

set -euo pipefail

cd "$(dirname "$0")/.."

GCP_PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
GCP_REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-schedule-portal}"
ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-https://schedule.giready.com,https://meds.giready.com}"
QR_BASE_URL="${QR_BASE_URL:-https://schedule.giready.com/v}"

if [[ -z "${GCP_PROJECT}" ]]; then
  echo "FATAL: no GCP project set. Run \`gcloud config set project ...\`" >&2
  exit 1
fi

IMAGE="gcr.io/${GCP_PROJECT}/${SERVICE_NAME}:$(date +%Y%m%d-%H%M%S)"

echo "→ Building image: ${IMAGE}"
gcloud builds submit --tag "${IMAGE}" .

echo "→ Deploying to Cloud Run (${GCP_REGION})"
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --region "${GCP_REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --min-instances 1 \
  --max-instances 5 \
  --concurrency 4 \
  --timeout 60s \
  --set-env-vars "ALLOWED_ORIGINS=${ALLOWED_ORIGINS},QR_BASE_URL=${QR_BASE_URL},PORTAL_SKILL_SOURCE=vendor"

URL=$(gcloud run services describe "${SERVICE_NAME}" --region "${GCP_REGION}" --format='value(status.url)')
echo
echo "✓ Deployed: ${URL}"
echo "  Healthz:  ${URL}/healthz"
echo
echo "Next: map the custom domain (one-time):"
echo "  gcloud beta run domain-mappings create \\"
echo "    --service ${SERVICE_NAME} \\"
echo "    --domain api-schedule.giready.com \\"
echo "    --region ${GCP_REGION}"
echo
echo "Then add the CNAME it prints to your Cloudflare DNS."
