# Legacy cleanup inventory

Baseline: `40689f03030d0ac7451226faaba11b74665a9057`. Only tracked source,
configuration, test, benchmark/demo, validation-fixture and documentation files
listed below are removed. Ignored caches, environments, generated products and all
MarineCast/Data files are excluded. Git history preserves removed code.

The supported path now has 17 Python files (three pipeline modules, compact
helpers/entry points, retained merger and transaction implementation), down from
120. Runtime dependencies fall from 12 to six: h3, numpy, numcodecs, pandas,
pyarrow and PyYAML. Removed paths were outside the approved product after extracting
its reducer, credential resolver, byte-hash and registry helpers. Tests specific
to retired products are removed; active behavior tests and transaction recovery
coverage are preserved. This is an intentional supported-scope reduction, not a
claim that every removed legacy function had no historical callers.

## Removed tracked paths

```text
CHANGELOG.md
PRODUCTION_READINESS.md
benchmarks/README.md
benchmarks/era5_aggregation.py
benchmarks/era5_arco.py
benchmarks/era5_backend_comparison.py
benchmarks/era5_backfill.py
benchmarks/native_processing.py
benchmarks/request_lifecycle.py
benchmarks/request_review.py
config/common.yaml
config/data/environment_meteorological.yaml
config/data/presentation_settings.yaml
config/feature_catalog.yaml
config/model_feature_policy.yaml
demo/hourly_week_offline.py
demo/qualify_requests.py
demo/request_offline.py
docs/CDS_JOB_INTEGRATION_AND_PILOTS.md
docs/CONFIGURATION.md
docs/DATA_SOURCES.md
docs/HRRR_NATIVE_DAY_PILOT.md
docs/IMPLEMENTATION_TRACKER.md
docs/PRODUCTS.md
docs/SETUP.md
docs/WORKFLOWS.md
docs/archived-validation.md
docs/daily-matrix.md
docs/development/release-process.md
docs/development/repository-presentation.md
docs/era5-backfill-performance.md
docs/era5-backfill.md
docs/era5-retrospective.md
docs/evidence/era5-backend-comparison-20261014.json
docs/evidence/era5-backfill-20261014.json
docs/evidence/era5-backfill-live-20261014.json
docs/evidence/era5-backfill-speed-20261014.json
docs/evidence/era5-cds-access-20261014.json
docs/evidence/request-lifecycle.json
docs/evidence/request-review-after.json
docs/evidence/request-review-before.json
docs/getting-started/quickstart.md
docs/guides/downstream-modeling.md
docs/hourly-weather.md
docs/limitations.md
docs/live-week-demo.md
docs/live-week-results.md
docs/marine-reporting.md
docs/methodology.md
docs/migration-inventory.json
docs/reference/api.md
docs/reference/cli.md
docs/reference/variables.md
docs/request-api.md
docs/request-cleanup.md
docs/request-handoff.md
docs/request-refactor-evidence.md
docs/request-review-evidence.md
docs/scientific-validation-plan.md
docs/shared-study.md
docs/weather-summaries.md
src/meteorology/DATA_SOURCES.md
src/meteorology/astronomy.py
src/meteorology/atmospheric_summaries.py
src/meteorology/backfill.py
src/meteorology/backfill_cli.py
src/meteorology/backfill_compute.py
src/meteorology/backfill_metrics.py
src/meteorology/backfill_plan.py
src/meteorology/backfill_resources.py
src/meteorology/capabilities.py
src/meteorology/climate_context.py
src/meteorology/components.py
src/meteorology/concurrency.py
src/meteorology/config.py
src/meteorology/core/config/__init__.py
src/meteorology/core/config/common_areas.py
src/meteorology/core/config/data.py
src/meteorology/core/config/document.py
src/meteorology/core/config/paths.py
src/meteorology/core/config/presentation.py
src/meteorology/core/data/__init__.py
src/meteorology/core/data/catalog.py
src/meteorology/core/data/contracts.py
src/meteorology/core/data/meteorological_schemas.py
src/meteorology/core/data/registry.py
src/meteorology/core/geo/__init__.py
src/meteorology/core/geo/h3.py
src/meteorology/daily_matrix.py
src/meteorology/daylight/__init__.py
src/meteorology/daylight/build.py
src/meteorology/daylight/compute.py
src/meteorology/daylight/features.py
src/meteorology/daylight/inspect.py
src/meteorology/daylight/validation.py
src/meteorology/era5/__init__.py
src/meteorology/era5/blocks.py
src/meteorology/era5/credentials.py
src/meteorology/era5/daily.py
src/meteorology/era5/execution.py
src/meteorology/era5/jobs.py
src/meteorology/era5/pilot.py
src/meteorology/era5/pilot_qa.py
src/meteorology/era5/plan.py
src/meteorology/era5/planning.py
src/meteorology/era5/resources.py
src/meteorology/era5/source.py
src/meteorology/era5/transport.py
src/meteorology/field_contracts.py
src/meteorology/hourly_weather/__init__.py
src/meteorology/hourly_weather/build.py
src/meteorology/hourly_weather/download.py
src/meteorology/hourly_weather/inspect.py
src/meteorology/hourly_weather/live.py
src/meteorology/hourly_weather/native_day.py
src/meteorology/hourly_weather/native_qa.py
src/meteorology/hourly_weather/native_source.py
src/meteorology/hourly_weather/product.py
src/meteorology/hourly_weather/retrospective_plan.py
src/meteorology/inspection.py
src/meteorology/lunar/__init__.py
src/meteorology/lunar/build.py
src/meteorology/lunar/compute.py
src/meteorology/lunar/inspect.py
src/meteorology/lunar/validation.py
src/meteorology/maintenance/__init__.py
src/meteorology/maintenance/benchmark_hrrr_r5_acquisition.py
src/meteorology/maintenance/update_environment_feature_catalog.py
src/meteorology/maintenance/validate_astronomy.py
src/meteorology/maintenance/validate_horizons.py
src/meteorology/maintenance/validate_ndbc.py
src/meteorology/methods.py
src/meteorology/migration.py
src/meteorology/modeling/__init__.py
src/meteorology/modeling/feature_policy.py
src/meteorology/offline_example.py
src/meteorology/precipitation_intervals.py
src/meteorology/releases.py
src/meteorology/request.py
src/meteorology/request_adapters.py
src/meteorology/request_cache.py
src/meteorology/request_cli.py
src/meteorology/request_lifecycle.py
src/meteorology/request_lineage.py
src/meteorology/request_sources.py
src/meteorology/request_storage.py
src/meteorology/request_vintage.py
src/meteorology/resources/config/common.yaml
src/meteorology/resources/config/data/environment_meteorological.yaml
src/meteorology/resources/config/data/presentation_settings.yaml
src/meteorology/resources/config/feature_catalog.yaml
src/meteorology/resources/config/model_feature_policy.yaml
src/meteorology/spatial_support/__init__.py
src/meteorology/spatial_support/build.py
src/meteorology/spatial_support/inspect.py
src/meteorology/surface_weather/__init__.py
src/meteorology/surface_weather/build.py
src/meteorology/surface_weather/download.py
src/meteorology/surface_weather/inspect.py
src/meteorology/surface_weather/sampling.py
src/meteorology/surface_weather/source.py
src/meteorology/surface_weather/source_validation.py
src/meteorology/surface_weather/storage.py
src/meteorology/surface_weather/time_series_map.py
src/meteorology/surface_weather/verify.py
src/meteorology/surface_weather/wind.py
src/meteorology/temporal_products.py
src/meteorology/validation.py
src/meteorology/variables.py
src/meteorology/weather_summary.py
src/meteorology/week_demo.py
src/meteorology/week_demo_report.py
tests/request_fixtures.py
tests/request_provider.py
tests/test_acquisition_interrupt_child.py
tests/test_atmospheric_summaries.py
tests/test_backend_benchmark.py
tests/test_backfill.py
tests/test_candidate_contract_regressions.py
tests/test_climate_context.py
tests/test_concurrency.py
tests/test_config_and_astronomy.py
tests/test_daily_matrix.py
tests/test_era5.py
tests/test_era5_credentials.py
tests/test_era5_jobs.py
tests/test_era5_pilot.py
tests/test_era5_resources.py
tests/test_era5_transport.py
tests/test_field_contracts.py
tests/test_hourly_weather_product.py
tests/test_hrrr_native_day.py
tests/test_hrrr_native_source.py
tests/test_independent_validation.py
tests/test_live_hrrr.py
tests/test_live_week_demo.py
tests/test_manifests.py
tests/test_meteorological_model_feature_policy.py
tests/test_offline_weather_fixture.py
tests/test_precipitation_intervals.py
tests/test_processing_scaling.py
tests/test_production_contracts.py
tests/test_request_api.py
tests/test_request_commit.py
tests/test_request_lifecycle.py
tests/test_request_lineage.py
tests/test_request_preflight.py
tests/test_request_qualification.py
tests/test_request_review.py
tests/test_request_storage.py
tests/test_request_transport.py
tests/test_shared_study.py
tests/test_source_and_catalog.py
tests/test_standalone_package.py
tests/test_surface_weather_time_series_map.py
tests/test_temporal_products.py
tests/test_weather_aggregation_parity.py
tests/test_weather_summary.py
validation/README.md
validation/acquire_ndbc_pilot.py
validation/pilot_inputs/hrrr_46088_2024-01-02_hourly.json
validation/references/jpl_horizons_2024_altitudes.json
validation/references/ndbc_46088_2024-01-02.meta.json
validation/references/ndbc_46088_2024-01-02.txt
validation/references/usno_2024_solar_lunar.json
validation/reports/coverage_scope_v1.json
validation/reports/jpl_horizons_2024_altitudes.json
validation/reports/ndbc_46088_2024-01-02.json
validation/reports/usno_2024_solar_lunar.json
validation/study_spec_v1.json
```

## Verification of this cleanup

The reduced suite passed 74 tests in the checkout and 74 against a freshly built
wheel outside it. Removed products' tests were retired, not reclassified as passing.
The cleaned installed wheel exactly reproduced all keys and values for 463 cells
on 2009-01-01 and 2026-10-09–10 (14 daily / 10 hourly fields), using retained native
sources with network forbidden. The source caches and approved Data files were
preserved. Lint, strict docs, wheel/sdist builds and CLI help passed. The wheel
contains 17 Python files; an import guard rejects retired optional dependencies.
No full historical rebuild or fresh provider qualification was performed.

Application/Modeling Python source search found no direct imports of the removed
toolkit interfaces; this is not exhaustive external-client qualification. Private
ad hoc scripts using removed getters require migration. The original two-epoch
source caches and offline merger are retained.
