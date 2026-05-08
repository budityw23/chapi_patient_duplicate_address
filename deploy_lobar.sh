#!/usr/bin/env bash
# Deploy CHAPI dedup-address for Lombok Barat to Cloud Run.
# Usage: ./deploy_lobar.sh
#
# The service starts with DRY_RUN=true. Flip with:
#   gcloud run services update dedup-address-chapi-lombok-barat \
#     --update-env-vars DRY_RUN=false --region asia-southeast2 --project spheres-lombok-barat
set -euo pipefail

CRED_FILE="$(dirname "$0")/credentials_lobar.json"
PROJECT="spheres-lombok-barat"
REGION="asia-southeast2"
SERVICE_NAME="dedup-address-chapi-lombok-barat"
FHIR_URL="${FHIR_URL_LOMBOK_BARAT:-https://spheres-chapi-fhir-58303528366.asia-southeast2.run.app/fhir}"
CHAPI_API_KEY="${CHAPI_API_KEY_LOMBOK_BARAT:-jYR8qCrzDWhaUe5Qg3xBvuKdbM6fS2L4EJ9nATGpkX7VsyctNm}"

MODE="${MODE:-incremental}"
DRY_RUN="${DRY_RUN:-true}"
FRESH="${FRESH:-false}"
CHECKPOINT_BUCKET="${CHECKPOINT_BUCKET:-dedup-patient}"

if [[ -f "$CRED_FILE" ]]; then
  gcloud auth activate-service-account --key-file="$CRED_FILE"
else
  echo "WARNING: credential file not found at $CRED_FILE — using current gcloud auth" >&2
fi

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
  --set-env-vars="MODE=${MODE},DRY_RUN=${DRY_RUN},FRESH=${FRESH},TENANT=lombok-barat,SERVER_KIND=chapi,FHIR_URL=${FHIR_URL},CHAPI_API_KEY=${CHAPI_API_KEY},CHECKPOINT_BUCKET=${CHECKPOINT_BUCKET},FHIR_CONCURRENCY=20"

echo
echo "Deployed: $SERVICE_NAME"
echo "Trigger a run with:"
echo "  curl -X POST \$(gcloud run services describe $SERVICE_NAME --region $REGION --project $PROJECT --format 'value(status.url)')/run"
