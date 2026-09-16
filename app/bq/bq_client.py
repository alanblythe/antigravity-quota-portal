"""BigQuery client for aggregating Antigravity inference token usage."""

import logging
from datetime import datetime

from google.cloud import bigquery
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class UserUsageRecord(BaseModel):
    user_email: str
    model_name: str
    request_count: int
    token_count: int
    last_active: datetime | None = None


class BigQueryUsageClient:
    """Production BigQuery client for querying inference audit logs."""

    def __init__(self, project_id: str, dataset_name: str):
        self.project_id = project_id
        self.dataset_name = dataset_name
        self.client = bigquery.Client(project=project_id)

    def fetch_weekly_usage(
        self,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[UserUsageRecord]:
        """Fetch aggregated token usage per user and model within the UTC time range."""
        query = f"""
        WITH raw_events AS (
          SELECT
            timestamp,
            REPLACE(labels.user_id, 'user:', '') AS user_email,
            COALESCE(labels.model, 'gemini-3.6-flash') AS model_name,
            COALESCE(
              SAFE_CAST(jsonpayload_v1_inferenceresponselog.metadata.totalTokenCount AS INT64),
              SAFE_CAST(jsonpayload_v1_inferenceresponselog.metadata.totaltokencount AS INT64),
              0
            ) AS total_tokens
          FROM
            `{self.project_id}.{self.dataset_name}.businessaicode_googleapis_com_inference_response`
          WHERE
            timestamp >= @start_timestamp_utc
            AND timestamp <= @end_timestamp_utc
        )
        SELECT
          user_email,
          model_name,
          COUNT(1) AS request_count,
          SUM(total_tokens) AS token_count,
          MAX(timestamp) AS last_active
        FROM
          raw_events
        WHERE
          user_email IS NOT NULL
          AND NOT ENDS_WITH(user_email, '.gserviceaccount.com')
        GROUP BY
          user_email,
          model_name;
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start_timestamp_utc", "TIMESTAMP", start_utc),
                bigquery.ScalarQueryParameter("end_timestamp_utc", "TIMESTAMP", end_utc),
            ]
        )

        try:
            query_job = self.client.query(query, job_config=job_config)
            results = query_job.result()

            records: list[UserUsageRecord] = []
            for row in results:
                records.append(
                    UserUsageRecord(
                        user_email=row["user_email"],
                        model_name=row["model_name"],
                        request_count=row["request_count"],
                        token_count=row["token_count"],
                        last_active=row["last_active"],
                    )
                )
            return records
        except Exception as e:
            logger.error(f"Error querying BigQuery usage: {e}", exc_info=True)
            return []
