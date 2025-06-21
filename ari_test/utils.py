#!/usr/bin/env python

import os
import unittest
from urllib.parse import urljoin # Changed from urlparse
import ari
import requests
import responses # Added


class AriTestCase(unittest.TestCase):
    """Base class for mock ARI server.
    """

    BASE_URL = "http://ari.py/ari"

    def setUp(self):
        """Setup responses; create ARI client.
        """
        super(AriTestCase, self).setUp()
        # It's common to use responses as a context manager in tests,
        # but for a base class setUp/tearDown, explicit start/stop is fine.
        self.responses_mock = responses.RequestsMock(assert_all_requests_are_fired=True)
        self.responses_mock.start()
        self.serve_api()
        # Assuming ari.connect will be updated or tests will mock it appropriately
        # For now, keeping this line as is.
        self.uut = ari.connect('http://ari.py/', 'test', 'test')

    def tearDown(self):
        """Cleanup.
        """
        super(AriTestCase, self).tearDown()
        self.responses_mock.stop()
        self.responses_mock.reset()

    @classmethod
    def build_url(cls, *args):
        """Build a URL, based off of BASE_URL, with the given args.

        >>> AriTestCase.build_url('foo', 'bar', 'bam', 'bang')
        'http://ari.py/ari/foo/bar/bam/bang'

        :param args: URL components
        :return: URL
        """
        url = cls.BASE_URL
        for arg_item in args: # Renamed arg to arg_item to avoid conflict if any stdlib arg is in scope
            url = urljoin(url + '/', str(arg_item)) # Use from urllib.parse import urljoin
        return url

    def serve_api(self):
        """Register all api-docs with responses to serve them for unit tests.
        """
        # This assumes 'sample-api' directory is relative to where tests are run.
        # It's better to make this path more robust if possible.
        sample_api_dir = os.path.join(os.path.dirname(__file__), '..', 'sample-api') # Adjust path if needed
        if not os.path.isdir(sample_api_dir):
            # Fallback for common execution directory (e.g. project root)
            sample_api_dir = 'sample-api'
            if not os.path.isdir(sample_api_dir):
                 log.warning(f"sample-api directory not found at {os.path.abspath(sample_api_dir)} or relative ./sample-api")
                 return # Cannot serve API docs

        for filename in os.listdir(sample_api_dir):
            if filename.endswith('.json'):
                with open(os.path.join(sample_api_dir, filename), 'r') as fp:
                    body_content = fp.read()
                # The URL for api-docs is relative to the base of ari.py, not BASE_URL here.
                # e.g. http://ari.py/ari/api-docs/resources.json
                # build_url constructs http://ari.py/ari/...
                # So, need to be careful with path segments.
                # If filename is 'resources.json', path should be 'api-docs/resources.json'
                # The original Client uses urljoin(base_url, "ari/api-docs/resources.json")
                # Here, base_url for client is 'http://ari.py/'
                # So, served URL should be 'http://ari.py/ari/api-docs/resources.json'
                # self.build_url('api-docs', filename) would give 'http://ari.py/ari/api-docs/filename'
                # This seems correct.
                self.serve(responses.GET, 'api-docs', filename, body=body_content)


    def serve(self, method, *args, **kwargs):
        """Serve a single URL for current test using responses.

        :param method: HTTP method (e.g., responses.GET, responses.POST).
        :param args: URL path segments.
        :param kwargs: See responses.add()
                       Typically: body (str), json (dict), status (int), content_type (str)
        """
        url = self.build_url(*args)

        # Default status for body-less responses if not provided
        if 'body' not in kwargs and 'json' not in kwargs and 'status' not in kwargs:
            kwargs['status'] = requests.codes.no_content # 204

        # Ensure content_type if json is provided
        if 'json' in kwargs and 'content_type' not in kwargs:
            kwargs['content_type'] = 'application/json'
        elif 'body' in kwargs and isinstance(kwargs['body'], str) and 'content_type' not in kwargs:
            # Default content type for string bodies if not specified, assuming JSON.
            # This might need to be more flexible if other body types are served.
            kwargs['content_type'] = 'application/json'

        self.responses_mock.add(method, url, **kwargs)
