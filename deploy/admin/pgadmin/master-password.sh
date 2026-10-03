#!/bin/sh
# MASTER_PASSWORD_HOOK for pgAdmin: print the host-generated encryption key.
exec cat /run/secrets/pgadmin_master_password
