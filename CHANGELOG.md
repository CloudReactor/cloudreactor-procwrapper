# Changelog

<!--next-version-placeholder-->

## v2.0.0-rc1 (2021-01-26)
* Initial version
## v2.0.0-rc2 (2021-01-31)
* Fix some property names for Task Execution creation
## v2.0.0-rc3 (2021-02-03)
* Fix more property names for Task Execution creation
* Document some environment variables
## v2.0.0-rc4 (2021-02-16)
* Enable runtime metadata fetching by default
* Fix issue parsing ECS metadata
## v2.0.0-rc5 (2021-02-16)
* Fix empty ECS launch type sent to API server
## v2.0.0-rc6 (2021-02-21)
* Don't start the process and exit immediately if we get a 409 Conflict from the API server, even if prevent_offline_execution is false
## v2.0.0 (2021-02-22)
* First official release
## v2.1.0-rc1 (2021-03-22)
* Support for auto-created and passive Tasks, eliminating the need
to change user's deployment processes
* Add ENV method of secret fetch -- lookup from another environment
variable usually set to a JSON-encoded object, so that jsonpath-ng
can be used to extract individual secrets
* Build standalone executables for Linux/AMD64 and Windows
* Print a final message with information about exit code and timing
* Add --version option
* Add many one letter command line option aliases
