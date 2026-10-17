# Coastal ERA5 weather

`get_weather(start_date, end_date, variable_list)` produces exact approved coastal
R5 hourly/daily weather using the fast ECMWF geographic source cache. The public
files are two Parquets and one catalog. Start with the [API](regional-api.md).

- [Current product and original source vintages](coastal-product.md)
- [Scientific contracts](CONTRACTS.md)
- [Implementation](ARCHITECTURE.md)
- [Testing and installation](DEVELOPMENT.md)
- [Migration from removed interfaces](MIGRATION.md)
- [Cleanup inventory](cleanup.md)

Weather is retrospective context, not species occurrence or forecast skill.
