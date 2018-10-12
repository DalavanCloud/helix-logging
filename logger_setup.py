#!/usr/bin/env python

import json
import urllib
import sys

import fastly
import boto3


class LoggerSetup(object):
    """
    Usage example:
    >>> namespace = 'XYZ'
    >>> version = 34
    >>> fastly_connector = LoggerSetup('XXXX')
    >>> fastly_connector.set_up_logging(namespace, version)
    """

    # Fastly constants.
    FASTLY_LOG_FORMAT = ('%l -- %t -- %R -- %a -- %D -- %>s -- %P -- %U -- %q -- %m -- %{Referer}i '
                         '-- %{User-agent}i -- %b -- %l -- %{Host}i')
    FASTLY_LOG_PERIOD = '60'
    FASTLY_LOG_NAME = 'S3 logging'

    # AWS constants.
    AWS_ACCOUNT_ID = '495798321641'
    AWS_LAMBDA_NAME = 'helix-logs-to-cosmos'
    AWS_BUCKET_PREFIX = 'helix-'
    AWS_DEFAULT_REGION = 'us-east-1'
    AWS_POLICY_NAME = '{}-policy-for-credentials'
    AWS_POLICY_ARN = 'arn:aws:iam::%s:policy/' % AWS_ACCOUNT_ID + AWS_POLICY_NAME

    # Platforms involved - logging purposes
    LOG_FASTLY = 'Fastly'
    LOG_AWS = 'AWS'
    LOG_WHISK = 'WHISK'


    def __init__(self, fastly_auth, aws_access_key, aws_secret_key, aws_region=AWS_DEFAULT_REGION):
        """
        Create a connection to Fastly and make sure the auth key is valid.
        """
        self._log(self.LOG_WHISK, 'Setting up LoggerSetup')
        self.fastly_auth = fastly_auth
        self.aws_region = aws_region

        # Checks that make sure we're able to perform the API requests needed.
        if self.aws_region != self.AWS_DEFAULT_REGION:
            raise ValueError('We only have implementation for the default AWS region!')

        # Set up Fastly client.
        self.fastly_client = fastly.API()
        self.fastly_client.authenticate_by_key(self.fastly_auth)

        self.AWS_ACCESS_KEY = aws_access_key
        self.AWS_SECRET_KEY = aws_secret_key
        self.AWS_DEFAULT_REGION = aws_region
        self.AWS_CREDENTIALS = {
            'aws_access_key_id': self.AWS_ACCESS_KEY,
            'aws_secret_access_key': self.AWS_SECRET_KEY,
            'region_name': self.AWS_DEFAULT_REGION
        }

        # Set up S3 client.
        self.s3_client = boto3.client('s3', **self.AWS_CREDENTIALS)
        self.s3_resource = boto3.resource('s3', **self.AWS_CREDENTIALS)

        # Set up lambda client.
        self.lambda_client = boto3.client('lambda', **self.AWS_CREDENTIALS)

        # Set up IAM client.
        self.iam_client = boto3.client('iam', **self.AWS_CREDENTIALS)

    def set_up_logging(self, namespace, version):
        """
        Sets up logging for the customer with the provided namespace and version,
        following the steps below:
            1. Create AWS S3 bucket
            2. Create AWS user and generate (key, secret) keys that can only access that bucket
            3. Make an API call to Fastly in order to enable logging to the created S3 bucket
        """
        service = self.fastly_client.service(namespace)

        # AWS bucket names need to be lower case.
        namespace_lower = namespace.lower()
        aws_namespace = 'helix-{}'.format(namespace_lower)

        # 1. Create AWS S3 bucket.
        self._create_bucket(aws_namespace)

        # 2. Create AWS user and generate (key, secret) keys that can only access that bucket.
        key, secret = self._create_credentials_for_bucket(aws_namespace)

        # 3. Make an API call to Fastly in order to enable logging to the created S3 bucket.
        self._log(self.LOG_FASTLY,
                  'Setting up logging configuration for Fastly namespace {}'.format(aws_namespace))
        body = {
            'bucket_name': aws_namespace,
            'access_key': key,
            'secret_key': secret,
            'domain': 's3.amazonaws.com',
            'format': self.FASTLY_LOG_FORMAT,
            'name': self.FASTLY_LOG_NAME,
            'period': self.FASTLY_LOG_PERIOD,
            'version': version
        }
        encoded_body = urllib.parse.urlencode(body)
        service.query(
            self.fastly_client.conn,
            '/service/{}/version/{}/logging/s3'.format(namespace, version),
            'POST',
            body=encoded_body
        )

    def tear_down_logging(self, namespace, version):
        """
        Tears down logging for the customer with the provided namespace and version,
        following the steps below:
            1. Remove the AWS S3 bucket and user
            2. Make an API call to Fastly in order to disable logging
        """
        service = self.fastly_client.service(namespace)

        # AWS bucket names need to be lower case.
        namespace_lower = namespace.lower()
        aws_namespace = 'helix-{}'.format(namespace_lower)

        # 1. Remove the AWS S3 bucket.
        self._remove_bucket(aws_namespace)

        # 2. Make an API call to Fastly in order to disable logging.
        self._log(self.LOG_FASTLY,
                  'Removed logging configuration for Fastly namespace {}'.format(aws_namespace))

    def _log(self, platform, message):
        kwargs = {'platform': platform, 'message': message}
        sys.stdout.write('[{platform}] {message}\n'.format(**kwargs))
        sys.stdout.flush()

    def _create_bucket(self, namespace):
        """
        Create customer's AWS S3 bucket and other objects associated with it.
        """
        if not namespace.startswith(self.AWS_BUCKET_PREFIX):
            raise ValueError('[AWS]    Bucket name should start with {}'.format(self.AWS_BUCKET_PREFIX))

        # 1. Create bucket.
        self._create_s3_bucket(namespace)

        # 2. Add an access policy.
        self._add_access_policy(namespace)

        # 3. Create function configuration.
        self._create_trigger_to_lambda_from_bucket(namespace)

    def _remove_bucket(self, namespace):
        """
        Remove customer's AWS S3 bucket and any objects associated with it.
        Note that we don't need to remove the trigger to lambda, because we're deleting the bucket.
        """
        if not namespace.startswith(self.AWS_BUCKET_PREFIX):
            raise ValueError('[AWS]    Bucket name should start with {}'.format(self.AWS_BUCKET_PREFIX))

        # 1. Remove bucket.
        self._remove_s3_bucket(namespace)

        # 2. Remove access policy from the lambda function.
        self._remove_access_policy(namespace)

    def _create_credentials_for_bucket(self, namespace):
        # 1. Create a policy that only allows request to the provided bucket.
        policy_arn = self._create_iam_policy_for_accessing_bucket(namespace)

        # 2. Create user and attach the previously-created policy ARN.
        self._create_iam_user(namespace, policy_arn)

        # 3. Generate (key, secret) for the newly created user.
        key, secret = self._create_key_secret_for_user(namespace)

        return key, secret

    def _create_s3_bucket(self, bucket):
        # Note: because we're using the default region, AWS does not accept LOCATION
        # ...:     CreateBucketConfiguration={
        # ...:         'LocationConstraint': 'us-east-1'
        # ...:     }
        self._log(self.LOG_AWS, 'Creating bucket {}'.format(bucket))
        response = self.s3_client.create_bucket(
            ACL='private',
            Bucket=bucket,
        )
        return response

    def _remove_s3_bucket(self, bucket):
        self._log(self.LOG_AWS, 'Fetching bucket {}'.format(bucket))
        bucket_obj = self.s3_resource.Bucket(bucket)
        s3_objects_in_bucket = []
        for item in self._get_s3_keys_as_generator(bucket):
            s3_objects_in_bucket.append({'Key': item})

        self._log(self.LOG_AWS, 'Removing contents in bucket {}'.format(bucket))
        bucket_obj.delete_objects(Delete={'Objects': s3_objects_in_bucket})

        self._log(self.LOG_AWS, 'Removing bucket {}'.format(bucket))
        bucket_obj.delete()

    def _get_s3_keys_as_generator(self, bucket):
        """Generate all the keys in an S3 bucket."""
        kwargs = {'Bucket': bucket}
        while True:
            resp = self.s3_client.list_objects_v2(**kwargs)
            for obj in resp['Contents']:
                yield obj['Key']

            try:
                kwargs['ContinuationToken'] = resp['NextContinuationToken']
            except KeyError:
                break

    def _add_access_policy(self, namespace):
        # An access policy associated with the lambda function will add a permission granting this
        # S3 bucket to access the lambda function.
        self._log(
            self.LOG_AWS,
            'Added access policy to grant S3 bucket {} access to the lambda function {}'.format(namespace, self.AWS_LAMBDA_NAME))
        source_arn = 'arn:aws:s3:::{}'.format(namespace)
        statement_id = 'lambda-{}-policy'.format(namespace)
        self.lambda_client.add_permission(
            FunctionName=self.AWS_LAMBDA_NAME,
            StatementId=statement_id,
            Action='lambda:InvokeFunction',
            Principal='s3.amazonaws.com',
            SourceArn=source_arn,
            SourceAccount=self.AWS_ACCOUNT_ID
        )

    def _remove_access_policy(self, namespace):
        """
        Remove access policy associated with the lambda function which granted the S3 bucket to access the lambda function.
        """
        self._log(
            self.LOG_AWS,
            'Removing access policy granting S3 bucket {} access to the lambda function {}'.format(
                namespace, self.AWS_LAMBDA_NAME))

        try:
            response = self.lambda_client.remove_permission(
                FunctionName=self.AWS_LAMBDA_NAME,
                StatementId=namespace)
        except self.lambda_client.exceptions.ResourceNotFoundException:
            self._log(self.LOG_AWS, 'No access policy found, nothing to remove')
            return

        if response['ResponseMetadata']['HTTPStatusCode'] != 204:
            raise ValueError('Could not remove access policy {} for '
                             'function {}'.format(namespace, self.AWS_LAMBDA_NAME))
        self._log(self.LOG_AWS, 'Removed access policy')

    def _create_trigger_to_lambda_from_bucket(self, namespace):
        self._log(
            self.LOG_AWS,
            'Added event trigger to the S3 bucket {}, triggering the lambda function {} to execute'.format(
                namespace, self.AWS_LAMBDA_NAME))

        lambda_function_arn = 'arn:aws:lambda:us-east-1:{}:function:{}'.format(
            self.AWS_ACCOUNT_ID, self.AWS_LAMBDA_NAME)
        notification_configuration = {
            'LambdaFunctionConfigurations': [
                {
                    'Events': [
                        's3:ObjectCreated:*',
                    ],
                    'LambdaFunctionArn': lambda_function_arn,
                }
            ]
        }

        self.s3_client.put_bucket_notification_configuration(
            Bucket=namespace,
            NotificationConfiguration=notification_configuration
        )

    def _create_iam_policy_for_accessing_bucket(self, namespace):
        # Create a policy that only allows request to the provided bucket.
        self._log(
            self.LOG_AWS,
            'Creating a policy for user {}, allowing user aceess only to her S3 bucket'.format(namespace)
        )
        resource_arn = 'arn:aws:s3:::{}/*'.format(namespace)
        policy = {
            'Version': '2012-10-17',
            'Statement': [
                {
                    'Sid': '1',
                    'Effect': 'Allow',
                    'Action': ['s3:PutObject'],
                    'Resource': [resource_arn],
                }
            ]
        }
        policy_json = json.dumps(policy, indent=2)
        policy_name = self.AWS_POLICY_NAME.format(namespace)
        policy_details = self.iam_client.create_policy(PolicyName=policy_name, PolicyDocument=policy_json)
        policy_arn = policy_details['Policy']['Arn']
        return policy_arn

    def _create_iam_user(self, namespace, policy_arn):
        # Create user and attach the previously-created policys.
        self._log(self.LOG_AWS, 'Creating user for Fastly namespace {}'.format(namespace))
        self.iam_client.create_user(
            UserName=namespace,
            PermissionsBoundary=policy_arn
        )
        self.iam_client.attach_user_policy(
            UserName=namespace,
            PolicyArn=policy_arn
        )

    def _remove_iam_user(self, namespace):
        """
        Steps for deleting the user:
            1. Detach previously attached policy
            2. remove user's access keys
            3. Remove the IAM user
        """
        # 1. Detach previously attached policy
        policy_name = self.AWS_POLICY_ARN.format(namespace)
        self._log(self.LOG_AWS, 'Detaching policy {} from user {}'.format(policy_name, namespace))
        try:
            self.iam_client.detach_user_policy(
                UserName=namespace,
                PolicyArn=policy_name
            )
        except self.iam_client.exceptions.NoSuchEntityException as exception:
            self._log(self.LOG_AWS, 'Policy {} was not found'.format(policy_name))

        # 2. Remove user's access keys.
        self._log(self.LOG_AWS, 'Removing all access keys from user {}...'.format(namespace))
        access_keys_paginator = self.iam_client.get_paginator('list_access_keys')
        for response in access_keys_paginator.paginate(UserName=namespace):
            for access_key in response['AccessKeyMetadata']:
                access_key_id = access_key['AccessKeyId']
                self._log(
                    self.LOG_AWS,
                    'Removing user\'s {} access key {}'.format(namespace, access_key_id)
                )
                self.iam_client.delete_access_key(
                    AccessKeyId=access_key_id,
                    UserName=namespace
                )

        # Remove the IAM user.
        self._log(self.LOG_AWS, 'Removing user {}'.format(namespace))
        self.iam_client.delete_user(UserName=namespace)
        self._log(self.LOG_AWS, 'User {} successfully removed'.format(namespace))

    def _create_key_secret_for_user(self, namespace):
        # Create a pair of (key, secret) access keys.
        self._log(self.LOG_AWS, 'Creating a pair of credentials for user {}'.format(namespace))
        access_key = self.iam_client.create_access_key(UserName=namespace)
        key = access_key['AccessKey']['AccessKeyId']
        secret = access_key['AccessKey']['SecretAccessKey']
        self._log(
            self.LOG_AWS,
            'Successfully created S3 bucket and (key, secret) credentials that can be added '
            'to the customer\'s Fastly configuration'
        )
        return key, secret
