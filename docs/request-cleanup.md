# Cleanup inventory

The current baseline already removed the historical generic stage/artifact classes,
H3 aliases, range wrappers and obsolete weather helpers. Those findings from older
branches are not newly counted as deletions. Scoped searches covered source, tests,
demos, README/docs, root exports, CLI dynamic dispatch, module entry points,
packaging and configuration. An AST/literal pass found no remaining private function
with only its declaration; this is a lead, not proof of dynamic reachability.

| File/symbol | Callers checked | Decision | Reason |
| --- | --- | --- | --- |
| `era5.pilot._credential` | sole pilot caller, source/tests/docs, exports and module CLI | Delete wrapper | Route directly to the shared credential resolver; explicit alias-file compatibility retained |
| Repeated CLI command/module maps | CLI parser, help, dispatch, family/standalone tests | Consolidate | One `CLI_MODULES` table owns dynamic legacy dispatch and help |
| Repeated ERA5 point-block writer loops | bounded executor and request adapter, pilot QA, operation-count test | Consolidate | `era5.blocks.write_point_blocks` owns physical row-group addressing and ordered native indices |
| `inspection`, `spatial_support.inspect` visualization imports | inspectors, CLI module imports, standalone import walk, map tests | Move dependency/imports | Folium/Branca/GeoPandas live in `visualization`; imports occur only when rendering |
| `core.artifacts.atomic_write_text/json`, family publisher | public exports, time-series maps, producers, acquisition/release tests | Keep | Active atomicity and transaction contracts |
| `core.data` enums/registry/catalog | producer schemas, dynamic dataset access, tests, public exports | Keep | Public contracts and dynamic callers; enum members are not dead internal functions |
| `core.config.common_areas` | config loaders, support/astronomy/build tests | Keep | Active geographic selection and compatibility |
| Migration, verification, validation and benchmarks | CLI_MODULES, module `main`, tests, workflows | Keep | Active explicit advanced entry points; no automatic execution/cleanup |
| Fixed native pilots and independent QA | native module CLIs, exact plans and generated-GRIB/mocked transport tests | Keep | Qualification fixtures and gates, separate from general getters |
| Legacy family root APIs | `__all__`, CLI, examples/docs, tests | Keep | Existing semantics and compatibility |
| Root and packaged `config/` copies | `test_packaged_defaults_match_checkout`, init, wheel smoke | Keep with enforced parity | Existing byte-parity gate is authoritative; no new competing configuration source |

No user archives, published products, caches or datasets were deleted or moved.
No code was removed solely because a static scanner failed to see a caller.
Core imports, planning, offline acquisition-fixture processing and package help are
verified without visualization dependencies in the fresh-wheel check. The core
still needs scientific array/Arrow/H3/projection/geometry libraries for processing;
this is not a claim that all geospatial dependencies are optional.
