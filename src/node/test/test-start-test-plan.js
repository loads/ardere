"use strict"
const AWS = require('aws-sdk-mock');
const LambdaTester = require('lambda-tester');

const assert = require('chai').assert;
const handler = require('../lib/start-test-plan.js').handler;

describe('start test plan', () => {
    let upload;

    before(() => {
        upload = AWS.mock('S3', 'upload', (params, callback) => {
            callback(null, "Uploaded");
        });
    });

    after(() => {
        AWS.restore('S3');
    });

    it('uploaded start file to s3', () => {
        return LambdaTester(handler)
            .event({bucket: 'foo', plan_uuid: 'abcd', run_uuid: 'efgh'})
            .expectResult((result) => {
                assert(upload.stub.calledOnce);
                let params = upload.stub.args[0][0];
                assert(params.Bucket == 'foo');
                assert(params.Key == 'abcd/efgh/RUN.TXT');
                assert(params.ACL.includes('public'));
                let start_time = parseInt(params.Body.toString());
                assert(start_time <= Math.ceil(new Date().getTime() / 1000));
            });
    });
});
