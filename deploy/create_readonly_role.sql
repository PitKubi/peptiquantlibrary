-- Run once as the postgres superuser on the SysQuan database host.
-- The library's sync reads exactly two tables; the role can touch nothing else,
-- so the public-facing app can never write to (or read the rest of) the
-- research database.
CREATE ROLE pql_reader WITH LOGIN PASSWORD 'CHANGE_ME';
GRANT CONNECT ON DATABASE sysquant TO pql_reader;
GRANT USAGE ON SCHEMA public TO pql_reader;
GRANT SELECT ON sysq_inhouse_peptideworkflowentry TO pql_reader;
GRANT SELECT ON sysq_inhouse_syntheticpeptideinventory TO pql_reader;
