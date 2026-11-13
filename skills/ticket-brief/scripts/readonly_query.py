"""Read-only SQL against a PostgreSQL database, run where the app's credentials already live.

Copy this file somewhere temporary, replace QUERY, then run it inside a process that already
has database credentials in its environment, for example an app container:

    kubectl exec -i deploy/<app> -c <container> -n <namespace> --context <context> -- python - < <copy>.py
    docker compose exec -T <service> python - < <copy>.py

Credentials come from DSN_ENV_VAR (a libpq URL or key=value DSN) when it is set, otherwise from
the standard libpq variables (PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD). Change
DSN_ENV_VAR when the app keeps its DSN under another name.

The session is opened read-only, so PostgreSQL rejects any write instead of running it, and a
statement timeout stops runaway queries. Output is tab-separated, with a header row.
"""

import os

try:
    import psycopg2 as driver
except ImportError:
    import psycopg as driver

QUERY = """
SELECT 1
"""

DSN_ENV_VAR = "DATABASE_URL"
STATEMENT_TIMEOUT_MS = 120_000


def connect():
    return driver.connect(
        os.environ.get(DSN_ENV_VAR, ""),
        options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS} -c default_transaction_read_only=on",
    )


def main():
    connection = connect()
    with connection, connection.cursor() as cursor:
        cursor.execute(QUERY)
        print("\t".join(column.name for column in cursor.description))
        for row in cursor.fetchall():
            print("\t".join(str(value) for value in row))
    connection.close()


if __name__ == "__main__":
    main()
