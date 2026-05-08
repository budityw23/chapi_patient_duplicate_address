#!/usr/bin/env bash
# Deploy CHAPI dedup-address Cloud Run Service for one tenant.
# Usage: ./deploy.sh <purbalingga|lombok-barat>
#
# The same image is used for both tenants; env vars differ.
# The service is created with DRY_RUN=true by default. Flip with:
#   gcloud run services update <service-name> --update-env-vars DRY_RUN=false --region asia-southeast2
set -euo pipefail

TENANT="${1:?usage: ./deploy.sh <purbalingga|lombok-barat>}"

REGION="asia-southeast2"
SERVICE_NAME="dedup-address-chapi-${TENANT}"

case "$TENANT" in
  purbalingga)
    CRED_FILE="$(dirname "$0")/budi-triwibowo-editor-credential.json"
    PROJECT="stellar-orb-451904-d9"
    FHIR_URL="https://fhir-server.dataspheres.purbalinggakab.go.id/fhir"
    CHAPI_API_KEY="${CHAPI_API_KEY_PURBALINGGA:-jYR8qCrzDWhaUe5Qg3xBvuKdbM6fS2L4EJ9nATGpkX7VsyctNm}"
    ;;
  lombok-barat)
    CRED_FILE="$(dirname "$0")/credentials_lobar.json"
    PROJECT="spheres-lombok-barat"
    FHIR_URL="${FHIR_URL_LOMBOK_BARAT:-https://spheres-chapi-fhir-58303528366.asia-southeast2.run.app/fhir}"
    CHAPI_API_KEY="${CHAPI_API_KEY_LOMBOK_BARAT:-jYR8qCrzDWhaUe5Qg3xBvuKdbM6fS2L4EJ9nATGpkX7VsyctNm}"
    ;;
  *)
    echo "unknown tenant: $TENANT  (expected: purbalingga | lombok-barat)" >&2
    exit 1
    ;;
esac

if [[ -f "$CRED_FILE" ]]; then
  gcloud auth activate-service-account --key-file="$CRED_FILE"
else
  echo "WARNING: credential file not found at $CRED_FILE — using current gcloud auth" >&2
fi

MODE="${MODE:-incremental}"
DRY_RUN="${DRY_RUN:-true}"
FRESH="${FRESH:-false}"
CHECKPOINT_BUCKET="${CHECKPOINT_BUCKET:-dedup-patient}"

# TODO: switch CHAPI_API_KEY to Secret Manager once the secret exists.
# For now we pass it as a plain env var to match the existing exploration
# scripts. Don't commit a tenant-specific override file with the real key.

gcloud run deploy "$SERVICE_NAME" \
  --source . \
  --project "$PROJECT" \
  --region "$REGION" \
  --timeout 3600 \
  --min-instances 1 \
  --port 8080 \
  --concurrency 80 \
  --memory 512Mi \
  --cpu 1 \
  --set-env-vars="MODE=${MODE},DRY_RUN=${DRY_RUN},FRESH=${FRESH},TENANT=${TENANT},SERVER_KIND=chapi,FHIR_URL=${FHIR_URL},CHAPI_API_KEY=${CHAPI_API_KEY},CHECKPOINT_BUCKET=${CHECKPOINT_BUCKET},FHIR_CONCURRENCY=20"

echo
echo "Deployed: $SERVICE_NAME"
echo "Trigger a run with:"
echo "  curl -X POST \$(gcloud run services describe $SERVICE_NAME --region $REGION --project $PROJECT --format 'value(status.url)')/run"
