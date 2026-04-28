#!/usr/bin/env bash
# Deploy CHAPI dedup-address Cloud Run Job for one tenant.
# Usage: ./deploy.sh <purbalingga|lombok-barat>
#
# The same image is used for both tenants; env vars differ.
# The job is created with DRY_RUN=true by default. Flip with:
#   gcloud run jobs deploy <job-name> --update-env-vars DRY_RUN=false --region asia-southeast2
set -euo pipefail

TENANT="${1:?usage: ./deploy.sh <purbalingga|lombok-barat>}"

PROJECT="stellar-orb-451904-d9"
REGION="asia-southeast2"
JOB_NAME="dedup-address-chapi-${TENANT}"

case "$TENANT" in
  purbalingga)
    FHIR_URL="https://fhir-server.dataspheres.purbalinggakab.go.id/fhir"
    CHAPI_API_KEY="${CHAPI_API_KEY_PURBALINGGA:-jYR8qCrzDWhaUe5Qg3xBvuKdbM6fS2L4EJ9nATGpkX7VsyctNm}"
    ;;
  lombok-barat)
    # TODO: fill in real values before deploying lombok-barat
    FHIR_URL="${FHIR_URL_LOMBOK_BARAT:?set FHIR_URL_LOMBOK_BARAT before deploying lombok-barat}"
    CHAPI_API_KEY="${CHAPI_API_KEY_LOMBOK_BARAT:?set CHAPI_API_KEY_LOMBOK_BARAT before deploying lombok-barat}"
    ;;
  *)
    echo "unknown tenant: $TENANT  (expected: purbalingga | lombok-barat)" >&2
    exit 1
    ;;
esac

MODE="${MODE:-incremental}"
DRY_RUN="${DRY_RUN:-true}"
CHECKPOINT_BUCKET="${CHECKPOINT_BUCKET:-dedup-patient}"

# TODO: switch CHAPI_API_KEY to Secret Manager once the secret exists.
# For now we pass it as a plain env var to match the existing exploration
# scripts. Don't commit a tenant-specific override file with the real key.

gcloud run jobs deploy "$JOB_NAME" \
  --source . \
  --project "$PROJECT" \
  --region "$REGION" \
  --task-timeout 3600 \
  --max-retries 0 \
  --memory 512Mi \
  --cpu 1 \
  --set-env-vars="MODE=${MODE},DRY_RUN=${DRY_RUN},TENANT=${TENANT},SERVER_KIND=chapi,FHIR_URL=${FHIR_URL},CHAPI_API_KEY=${CHAPI_API_KEY},CHECKPOINT_BUCKET=${CHECKPOINT_BUCKET}"

echo
echo "Deployed: $JOB_NAME"
echo "Trigger a one-shot run with:"
echo "  gcloud run jobs execute $JOB_NAME --region $REGION --project $PROJECT"
