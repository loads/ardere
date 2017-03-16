# ardere
*AWS Serverless Service for Load-Testing*

ardere runs as a serverless service using AWS to orchestrate
load-tests consisting of docker container configurations arranged as
test plans.

## Installation

To deploy ardere to your AWS account, you will need a fairly recent
install of Node, then install the Node packages required:

    $ npm install
    
You will need to ensure your have AWS access and secret keys configured
for serverless:

    $ sls config
    
To deploy the ardere lambda's and required AWS stack:

    $ sls deploy

Then you can deploy the ardere Step Function:

    $ sls deploy stepf


## Developing

ardere is written in Python and deployed via serverless to AWS. To an
extent testing it on AWS is the most reliable indicator it works as
intended. However, there are sets of tests that ensure the Python code
is valid and works with arguments as intended that may be run locally.

Create a Python virtualenv, and install the test requirements:

    $ virtualenv ardenv
    $ source ardenv/bin/activate
    $ pip install -r test-requirements.txt

The tests can now be run with nose:

    $ nosetests
   
Note that **you cannot run the sls deploy while the virtualenv is active**
due to how the serverless Python requirements plugin operates.
