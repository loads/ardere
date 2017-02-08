"use strict"
const AWS = require('aws-sdk');
const path = require('path');
const util = require('util');

const START_FN = 'RUN.TXT'

exports.handler = (event, context, callback) => {
    let s3 = new AWS.S3({apiVersion: '2006-03-01'});
    let key = path.posix.join(event.plan_uuid, event.run_uuid, START_FN);
    let now = Math.round(new Date().getTime() / 1000);
    let s3_url = util.format("s3://%s/%s", event.bucket, key);

    s3.upload({
        Bucket: event.bucket,
        Key: key,
        Body: new Buffer(now.toString(), 'binary'),
        ACL: 'public-read'
    }).promise().then((data) => {
        console.log("Uploaded: ", s3_url);
        callback();
    }).catch((err) => {
        callback(err, "Error while uploading to: ", s3_url);
    });
}
