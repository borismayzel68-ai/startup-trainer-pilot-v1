"""HQ-018 · E-1: синтетический seed. Все записи вымышлены (домен example.test), is_synthetic=TRUE. Идемпотентно."""
import json
import os
import sys

USERS = [
    ("founder.synth@example.test", "Синтетический основатель", "founder"),
    ("investor.synth@example.test", "Синтетический фонд", "investor"),
    ("expert.synth@example.test", "Синтетический эксперт", "expert"),
    ("signer.synth@example.test", "Синтетический подписант", "signer"),
    ("clinic.synth@example.test", "Синтетическая клиника", "clinic"),
    ("syndicator.synth@example.test", "Синтетический синдикатор", "syndicator"),
]


def main() -> None:
    url = os.environ.get("SESSION_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("нужен SESSION_DATABASE_URL")
    import psycopg

    with psycopg.connect(url) as conn:
        for email, name, role in USERS:
            uid = conn.execute(
                "INSERT INTO users (email, display_name, is_synthetic) VALUES (%s,%s,TRUE) "
                "ON CONFLICT (email) DO UPDATE SET display_name=EXCLUDED.display_name RETURNING id",
                (email, name)).fetchone()[0]
            conn.execute("INSERT INTO role_grants (user_id, role_code) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                         (uid, role))
        conn.execute(
            "INSERT INTO jobs (kind, dedup_key, payload) VALUES ('tilda_intake', 'synthetic-tranid-0001', %s) "
            "ON CONFLICT (kind, dedup_key) DO NOTHING",
            (json.dumps({"tranid": "synthetic-tranid-0001", "project_name": "Синтетический проект"}),))
        conn.commit()
    print("seed ok (synthetic)")


if __name__ == "__main__":
    main()
