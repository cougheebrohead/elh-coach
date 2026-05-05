-- Recovery / readiness columns for biometrics.
-- Powers the daily readiness score (fitapp_core.recovery_score) plus
-- the trend graphs in client and trainer consoles.

alter table biometrics add column if not exists hrv_rmssd_ms numeric(5,1);
alter table biometrics add column if not exists sleep_hours  numeric(3,1);
alter table biometrics add column if not exists steps        int;
alter table biometrics add column if not exists active_kcal  int;
