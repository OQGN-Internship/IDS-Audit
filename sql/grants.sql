-- Gives ids_app its rights on every table and view, then takes back the ones
-- it must not have (ENG-01 6.3). Runs as ids_owner after every migration, in
-- one transaction (psql -1), so no insert-only table ever has its UPDATE back.
-- It can run any number of times: granting or revoking a right a second time
-- changes nothing.
--
-- Plain SQL only, with no psql commands, so the tests' Python fixture can run
-- it too (ENG-01 7.5).

-- 1. Every table and view, including any created before the default
--    privileges existed. In PostgreSQL, ALL TABLES includes views.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ids_app;
REVOKE TRUNCATE ON ALL TABLES IN SCHEMA public FROM ids_app;

-- 2. The insert-only tables: each row is written once and never changed.
REVOKE UPDATE, DELETE
    ON audit_event, posting, gl_ci_map, cc_dept_map, skipped_row, ruleset
    FROM ids_app;

-- 3. Every view. A simple view passes a write through to its table, with the
--    rights of the view's owner, ids_owner, so ids_app may only read it.
DO $$
DECLARE
    v record;
BEGIN
    FOR v IN SELECT schemaname, viewname FROM pg_views WHERE schemaname = 'public'
    LOOP
        EXECUTE format(
            'REVOKE INSERT, UPDATE, DELETE ON %I.%I FROM ids_app',
            v.schemaname,
            v.viewname
        );
    END LOOP;
END
$$;
