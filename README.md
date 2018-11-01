# ardere
*AWS Serverless Service for Load-Testing*

ardere runs as a serverless service using AWS to orchestrate
load-tests consisting of docker container configurations arranged as
test plans.

## Installation

Pre-requisite: 
installation requires node > v6 

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

## Run Test

1. Login to AWS console
   (mozilla-services use: stage)
2. Go to Step Functions > Dashboard
3. Select your state machine
   (mozilla-services use: "ardere-dev-ardere")
4. Click on "New Execution" button
5. Paste your json config into text area
   (example: [**mozilla-services/screenshots-loadtests** /ardere.json](https://github.com/mozilla-services/screenshots-loadtests/blob/master/ardere.json))
6. Optional: Assign a name to your execution
7. Click on "Start Execution"
8. Monitor execution in Dashboard
9. Test load should be visible in DataDog, NewRelic, etc.

## Monitoring

### Metrics Node Monitoring (Grafana) 

1. ssh -L 3000:\<ip\_metrics\_node\>:3000  \<ip\_bastion\_host\>
2. open local browser to http://localhost:3000
3. login using credentials specified in your ardere (JSON) config file
