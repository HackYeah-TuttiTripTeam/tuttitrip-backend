-- Privileges of the tuttitrip_worker role in one environment database.
-- Applied by deploy.sh after every migration run (idempotent):
--   psql -v dbname=<database> -f deploy/worker-grants.sql
-- The backend role owns every table; the worker never runs DDL.
-- tests/test_worker_grants.py checks that every table named here exists.

GRANT CONNECT ON DATABASE :"dbname" TO tuttitrip_worker;
GRANT USAGE ON SCHEMA public TO tuttitrip_worker;

-- Read-only: domain data the workflows need as input.
GRANT SELECT
    ON public.trips, public.profiles, public.pasted_documents,
       public.places, public.cities, public.place_prices, public.transit_fares
    TO tuttitrip_worker;

-- Catalog import (OSM, sheet): no DELETE. Idempotency keys are
-- places(source, source_key) and places(osm_type, osm_id).
GRANT INSERT, UPDATE
    ON public.places, public.cities, public.place_prices
    TO tuttitrip_worker;

-- Read-write: tables designated for worker output.
GRANT SELECT, INSERT, UPDATE, DELETE
    ON public.worker_heartbeats, public.job_results, public.embeddings
    TO tuttitrip_worker;
