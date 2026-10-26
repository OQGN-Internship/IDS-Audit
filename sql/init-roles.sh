#!/bin/bash
# Runs once, when db first starts with an empty pgdata volume (ENG-01 7.1).
# Creates the two roles and the application database, then gives ids_app its
# rights on every table ids_owner will create (ENG-01 6.3).
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
-- Read from the container's environment, which Compose fills from .env.
\getenv db_name DB_NAME
\getenv owner_password DB_OWNER_PASSWORD
\getenv app_password DB_APP_PASSWORD

CREATE ROLE ids_owner LOGIN CREATEDB PASSWORD :'owner_password';
CREATE ROLE ids_app LOGIN PASSWORD :'app_password';

-- Lets ids_owner switch to ids_app with SET ROLE, for the grant tests.
GRANT ids_app TO ids_owner;

CREATE DATABASE :"db_name" OWNER ids_owner;

-- Default privileges belong to one database, so set them from inside it.
\connect :"db_name"
ALTER DEFAULT PRIVILEGES FOR ROLE ids_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ids_app;
SQL
