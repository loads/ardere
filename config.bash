#! /bin/bash -w

ctrlc()
{
    echo "  Exiting..."
    rm ~/.aws/credentials
    exit 1
}
set -e

if [[ "`which serverless`" == "" ]]
then
    echo "Hrm, serverless is not installed. "
    echo "See https://serverless.com/framework/docs/providers/aws/guide/installation/"
    return
fi
if [[ ! -e ~/.aws/credentials ]]
then
    trap ctrlc SIGINT
    echo "  credential file was not found. Let's make one."
    echo ""
    echo "  If you haven't already, you'll need to create an access key."
    echo "  e.g. go to https://console.aws.amazon.com/iam/home#/users/${USER}/?security_credientials"
    echo "  and click [Create access key]."
    echo ""
    read -p "Access Key ID: " access_key
    read -p "Secret Key ID: " secret_key
    echo "  Thanks! Running configuration";
    echo serverless config credentials --provider aws --key $access_key --secret $secret_key
    serverless config credentials --provider aws --key $access_key --secret $secret_key
fi
echo "  You're configured. The next step is to deploy."

