-- Privileges of the tuttitrip_worker role in one environment database.
-- Applied by deploy.sh after every migration run (idempotent):
--   psql -v dbname=<database> -f deploy/worker-grants.sql
-- The backend role owns every table; the worker never runs DDL.
-- tests/test_worker_grants.py checks that every table named here exists.

GRANT CONNECT ON DATABASE :"dbname" TO tuttitrip_worker;
GRANT USAGE ON SCHEMA public TO tuttitrip_worker;

-- Read-only: domain data the workflows need as input.
GRANT SELECT
    ON public.trips, public.profiles, public.pasted_documents, public.expense_evidence,
       public.places, public.cities, public.place_prices, public.transit_fares
    TO tuttitrip_worker;

-- Catalog import: no DELETE. Idempotency keys are places(source, source_key)
-- and places(osm_type, osm_id) (uq_places_osm_type). The OSM import
-- (tuttitrip-worker fetch_place_candidates) inserts cities and never updates
-- them; it updates places only in the columns its upsert sets, so it cannot
-- move a place to another city or turn a row into a verified or sheet one.
GRANT INSERT
    ON public.places, public.cities, public.place_prices
    TO tuttitrip_worker;
GRANT UPDATE
    ON public.place_prices
    TO tuttitrip_worker;
GRANT UPDATE (name, category, tags, lat, lon, wheelchair, indoor, cuisine,
              diet_tags, amenities, opening_hours)
    ON public.places
    TO tuttitrip_worker;

-- OSM fetch state (replaces the osm-fetch:/osm-attempt: rows of job_results).
GRANT SELECT, INSERT, UPDATE
    ON public.city_fetches, public.city_fetch_attempts
    TO tuttitrip_worker;

-- Notifications: the worker creates them (workflow results) and the retention
-- job deletes old ones. It never updates them (reading is the user's business).
GRANT SELECT, INSERT, DELETE
    ON public.notifications
    TO tuttitrip_worker;

-- Read-write: tables designated for worker output.
GRANT SELECT, INSERT, UPDATE, DELETE
    ON public.worker_heartbeats, public.job_results, public.embeddings
    TO tuttitrip_worker;
