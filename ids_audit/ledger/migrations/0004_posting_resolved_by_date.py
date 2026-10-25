# posting_resolved, worked out faster (ENG-03 10): the same rows as 0002, with
# a posting's replacement found by comparing its date with the dates later
# uploads cover, not by working out every posting's month. Its columns don't
# change, so the view is replaced in place, and its reverse is 0002's view.

import importlib

from django.db import migrations

PREVIOUS = importlib.import_module(
    "ids_audit.ledger.migrations.0002_posting_resolved"
).CREATE_VIEW.replace("CREATE VIEW", "CREATE OR REPLACE VIEW")

CREATE_VIEW = """
CREATE OR REPLACE VIEW posting_resolved AS
WITH
-- Each report's latest ruleset version decides its mapping_rule (ENG-02 3).
latest_ruleset AS (
    SELECT DISTINCT ON (report_id)
        report_id,
        settings ->> 'mapping_rule' AS mapping_rule
    FROM ruleset
    ORDER BY report_id, version DESC
),
-- Only committed uploads count, replace others or give a mapping (10.2, 8.1).
-- The months an upload covers run from the first day of its first month to
-- the day after its last month ends.
committed AS (
    SELECT
        id,
        report_id,
        number,
        decided_at,
        date_trunc('month', period_start::timestamp)::date AS first_day,
        (date_trunc('month', period_end::timestamp) + interval '1 month')::date
            AS end_day
    FROM upload
    WHERE status = 'committed'
),
-- The latest rule: each report's most recently committed upload that has a GL
-- mapping, and the same for the cost center mapping (8.1). The order of
-- commits is decided_at, then number (10.2).
latest_gl_map AS (
    SELECT DISTINCT ON (u.report_id) u.report_id, u.id AS upload_id
    FROM committed u
    WHERE EXISTS (SELECT 1 FROM gl_ci_map m WHERE m.upload_id = u.id)
    ORDER BY u.report_id, u.decided_at DESC, u.number DESC
),
latest_cc_map AS (
    SELECT DISTINCT ON (u.report_id) u.report_id, u.id AS upload_id
    FROM committed u
    WHERE EXISTS (SELECT 1 FROM cc_dept_map m WHERE m.upload_id = u.id)
    ORDER BY u.report_id, u.decided_at DESC, u.number DESC
),
-- One row per report: its mapping rule, and the uploads whose mappings the
-- latest rule takes, so each posting needs one join for all three.
report_maps AS (
    SELECT
        r.id AS report_id,
        rules.mapping_rule,
        latest_gl_map.upload_id AS gl_upload_id,
        latest_cc_map.upload_id AS cc_upload_id
    FROM report r
    LEFT JOIN latest_ruleset rules ON rules.report_id = r.id
    LEFT JOIN latest_gl_map ON latest_gl_map.report_id = r.id
    LEFT JOIN latest_cc_map ON latest_cc_map.report_id = r.id
),
-- In replace-by-period mode, each committed upload with each upload committed
-- after it in the same report, and the dates the later one covers (7.3). In
-- line-key mode nothing is replaced, so its reports have no rows here. It's
-- worked out once per query, however PostgreSQL then joins it to postings.
replacing AS MATERIALIZED (
    SELECT u.id AS upload_id, later.first_day, later.end_day
    FROM committed u
    JOIN report r ON r.id = u.report_id
    JOIN committed later
        ON later.report_id = u.report_id
        AND (later.decided_at, later.number) > (u.decided_at, u.number)
    WHERE r.duplicate_mode <> 'line_key'
),
-- The postings that count (10.2), each with the upload whose GL mapping, and
-- whose cost center mapping, applies to it (8.1). own_upload uses the posting's
-- own upload when that upload has the mapping, and the latest rule otherwise.
counted AS (
    SELECT
        p.*,
        CASE
            WHEN maps.mapping_rule = 'own_upload'
                AND EXISTS (SELECT 1 FROM gl_ci_map m WHERE m.upload_id = p.upload_id)
            THEN p.upload_id
            ELSE maps.gl_upload_id
        END AS gl_map_upload_id,
        CASE
            WHEN maps.mapping_rule = 'own_upload'
                AND EXISTS (SELECT 1 FROM cc_dept_map m WHERE m.upload_id = p.upload_id)
            THEN p.upload_id
            ELSE maps.cc_upload_id
        END AS cc_map_upload_id
    FROM posting p
    JOIN committed u ON u.id = p.upload_id
    JOIN report_maps maps ON maps.report_id = p.report_id
    -- A posting is replaced when an upload committed after its own covers its
    -- date's month. Comparing its date with the dates the later upload covers
    -- gives the same answer as comparing months, and with nothing else in
    -- the condition, PostgreSQL can check every posting in one pass.
    WHERE NOT EXISTS (
        SELECT 1
        FROM replacing later
        WHERE later.upload_id = p.upload_id
            AND p.posting_date >= later.first_day
            AND p.posting_date < later.end_day
    )
)
-- Every posting column unchanged, in the table's order, then the mapping in
-- force (8.2). Empty text is '', never NULL, as in the tables (8.4).
SELECT
    p.id,
    p.sheet,
    p.row_number,
    p.company_code,
    p.fiscal_year,
    p.document_number,
    p.line_item,
    p.line_key,
    p.posting_date,
    p.gl_code,
    p.cost_center_code,
    p.value_type,
    p.is_actual,
    p.amount,
    p.currency,
    p.item_text,
    p.report_id,
    p.upload_id,
    COALESCE(g.gl_name, '') AS gl_name,
    COALESCE(g.ci_code, '') AS ci_code,
    CASE WHEN g.ci_code <> '' THEN g.ci_name ELSE '' END AS ci_name,
    CASE
        WHEN g.id IS NULL THEN 'missing_from_gl_map'
        WHEN g.ci_code = '' THEN 'listed_without_ci'
        ELSE 'mapped'
    END AS ci_status,
    COALESCE(c.cc_name, '') AS cc_name,
    COALESCE(c.dept_code, '') AS dept_code,
    COALESCE(c.dept_name, '') AS dept_name,
    -- outside_ids_rule has one value, missing_from_cc_map, so it's applied
    -- directly. A new value needs a new view migration (ENG-02 3).
    c.id IS NULL AS is_outside_ids
FROM counted p
LEFT JOIN gl_ci_map g
    ON g.upload_id = p.gl_map_upload_id AND g.gl_code = p.gl_code
LEFT JOIN cc_dept_map c
    ON c.upload_id = p.cc_map_upload_id AND c.cc_code = p.cost_center_code
"""


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0003_postingresolved"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_VIEW, reverse_sql=PREVIOUS),
    ]
