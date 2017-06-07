<a name="0.1.1"></a>
## 0.1.1 (2017-05-15)


#### Doc

*   update README with run steps ([3d6b5aa2](https://github.com/loads/ardere/commit/3d6b5aa2e6277a33e1a464d30168bbc2f406c512))

#### Bug Fixes

*   bump wait for cluster ready from 10 to 30 minutes ([a23115b8](https://github.com/loads/ardere/commit/a23115b8bc20f4e7b44ef4bf78b3687069ea1253))



<a name="0.1"></a>
## 0.1 (2017-04-25)


#### Features

*   secure metrics/load-test nodes from outside access ([3d08dccd](https://github.com/loads/ardere/commit/3d08dccd2376f85976b2f7bd026295c504560485), closes [#54](https://github.com/loads/ardere/issues/54))
*   Check names for invalid characters and lengths ([c886f6a9](https://github.com/loads/ardere/commit/c886f6a9598badb084871720515ff1663e61c032))
*   use security groups to restrict node access ([6395f9cd](https://github.com/loads/ardere/commit/6395f9cd52ab0c74a2735a8fecc2b30a217ddfda), closes [#48](https://github.com/loads/ardere/issues/48))
*   add grafana dashboarding ([a7a30df8](https://github.com/loads/ardere/commit/a7a30df8210429341e711ad713510e00acdc80c1), closes [#40](https://github.com/loads/ardere/issues/40))
*   add telegraf setup for per-container stat reporting ([7749e2eb](https://github.com/loads/ardere/commit/7749e2eb373a6f6afc49b2a7d03fcf5c4f9a18fb), closes [#33](https://github.com/loads/ardere/issues/33))
*   start influxdb with test runs ([8ddc48b5](https://github.com/loads/ardere/commit/8ddc48b5d3d395d54a914166e9803a6ab41ecf3f), closes [#19](https://github.com/loads/ardere/issues/19))
*   validate test plan before running ([0314fae7](https://github.com/loads/ardere/commit/0314fae70962f6281a261499e32500291ff764ab), closes [#21](https://github.com/loads/ardere/issues/21))
*   remove need to specify cpu_units ([e99eddea](https://github.com/loads/ardere/commit/e99eddead4b4119e508546aa38dc34873efa9632), closes [#20](https://github.com/loads/ardere/issues/20))
*   add port mapping for containers ([af054af1](https://github.com/loads/ardere/commit/af054af18e6ab5e4cd163c903867dc2cfe415168), closes [#24](https://github.com/loads/ardere/issues/24))
*   add toml loading as a test plan ([8342cb11](https://github.com/loads/ardere/commit/8342cb11902f6a225925cd1f8fd430d31a614cf9), closes [#32](https://github.com/loads/ardere/issues/32))
*   use cloudwatch logs for container output ([8bafa09f](https://github.com/loads/ardere/commit/8bafa09f82ad0116e31cc49849b7bd679219506c), closes [#27](https://github.com/loads/ardere/issues/27))
*   setup environment data from the test plan ([7e2ad2da](https://github.com/loads/ardere/commit/7e2ad2dad361336a4d46166e6aec32cd80c15e03), closes [#25](https://github.com/loads/ardere/issues/25))
*   fixup readme and test suite ([047a7fa6](https://github.com/loads/ardere/commit/047a7fa6381f4d034fd0c2955e90319a29730c76), closes [#22](https://github.com/loads/ardere/issues/22))
*   create MVP using serverless w/python ([9aa80467](https://github.com/loads/ardere/commit/9aa80467ce86b95e330886c1dcf57e5d84004e83), closes [#17](https://github.com/loads/ardere/issues/17))
*   add the lambda to start the run by writing to s3 ([e45a2789](https://github.com/loads/ardere/commit/e45a278930589b8dddbf88e3fe151f979d388edd))
*   add lambda function and basic CF templates for use ([0cb63bff](https://github.com/loads/ardere/commit/0cb63bff8f1d7b2533ee40a81a932e3bb618236f), closes [#11](https://github.com/loads/ardere/issues/11))
*   add an initial state machine impl ([2f571b0a](https://github.com/loads/ardere/commit/2f571b0aec7df9252c8d0fce44da252c17985fa2))
*   initial waiter script (#9) ([c07749c0](https://github.com/loads/ardere/commit/c07749c06a97bba50fe1701a2896d9b5a11dd18e))

#### Doc

*   update for use of cloud formation in setup (#2) ([243a4a11](https://github.com/loads/ardere/commit/243a4a11da3343735815dd42a0c78bb6936adf56))
*   initial design docs from autoconf ([eead6dd8](https://github.com/loads/ardere/commit/eead6dd80a43c24b40047fc5c22571122878ce05))

#### Bug Fixes

*   check service drained vs container draining ([fd4907e1](https://github.com/loads/ardere/commit/fd4907e10be9103cc9e20511c2e16c4ae906e469), closes [#62](https://github.com/loads/ardere/issues/62))
*   Do not check 'metrics' instance for draining ([40e8cd01](https://github.com/loads/ardere/commit/40e8cd01fc996f1596370c5ddb6ff6998b04ffdc))
*   Ensure all containers drained before exiting ([4cbea2fd](https://github.com/loads/ardere/commit/4cbea2fd0a280993d4312f82ba52354a0bf15f7f))
*   add proper tagging and socket limits ([15dc023e](https://github.com/loads/ardere/commit/15dc023efc91a0b3b644084a71f3f6f46be77158), closes [#44](https://github.com/loads/ardere/issues/44))



