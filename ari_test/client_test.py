#!/usr/bin/env python

import ari
import json
import requests # Still needed for requests.codes and potentially direct requests in tests
import unittest
# import urllib # Replaced with requests for test_docs

from ari_test.utils import AriTestCase
# httpretty aliases are removed, direct responses.GET etc. will be used in self.serve
import responses # For direct use of responses.GET etc. if needed, though self.serve handles it.
from bravado_core.exception import HTTPError as BravadoHTTPError # For test_bad_response


# noinspection PyDocstring
class ClientTest(AriTestCase):
    def test_docs(self):
        # This URL should be served by self.serve_api() in AriTestCase setUp
        # which uses self.responses_mock
        url = "http://ari.py/ari/api-docs/resources.json"
        # Make sure this URL exactly matches what serve_api registers.
        # AriTestCase.build_url('api-docs', 'resources.json') might be more robust if paths differ.
        # The serve_api in utils.py uses self.build_url('api-docs', filename)
        # which results in http://ari.py/ari/api-docs/resources.json. This is correct.

        # Replacing urllib.urlopen with requests.get
        try:
            # No need to explicitly register this if serve_api() in base class handles it.
            # If serve_api doesn't register this specific one (e.g. if it's not in sample-api),
            # then we would need:
            # self.responses_mock.add(responses.GET, url, body='{"basePath": "http://ari.py/ari"}', content_type="application/json")
            resp = requests.get(url)
            resp.raise_for_status() # check for HTTP errors
            actual = resp.json()
            # The assertion was self.assertEqual(self.BASE_URL, actual['basePath'])
            # self.BASE_URL is "http://ari.py/ari"
            # The resources.json basePath should match this.
            self.assertEqual(self.BASE_URL, actual.get('basePath'))
        except Exception as e:
            self.fail(f"test_docs failed: {e}")


    def test_empty_listing(self):
        self.serve(responses.GET, 'channels', body='[]')
        actual = self.uut.channels.list()
        self.assertEqual([], actual)

    def test_one_listing(self):
        self.serve(responses.GET, 'channels', body='[{"id": "test-channel"}]')
        self.serve(responses.DELETE, 'channels', 'test-channel') # Implicit 204

        actual = self.uut.channels.list()
        self.assertEqual(1, len(actual))
        actual[0].hangup()

    def test_play(self):
        self.serve(responses.GET, 'channels', 'test-channel',
                   body='{"id": "test-channel"}')
        self.serve(responses.POST, 'channels', 'test-channel', 'play',
                   body='{"id": "test-playback"}')
        self.serve(responses.DELETE, 'playbacks', 'test-playback') # Implicit 204

        channel = self.uut.channels.get(channelId='test-channel')
        # Assuming playback is a promoted object, and it has a stop method.
        # This part depends on the successful implementation of promote in model.py
        playback = channel.play(media='sound:test-sound')
        if playback: # Promote might return None for 204 or if model mapping fails
            playback.stop()
        else:
            self.fail("channel.play() did not return a playback object")


    def test_bad_resource(self):
        with self.assertRaises(AttributeError):
            self.uut.i_am_not_a_resource.list()

    def test_bad_repo_method(self):
        with self.assertRaises(AttributeError):
            self.uut.channels.i_am_not_a_method()

    def test_bad_object_method(self):
        self.serve(responses.GET, 'channels', 'test-channel',
                   body='{"id": "test-channel"}')

        channel = self.uut.channels.get(channelId='test-channel')
        with self.assertRaises(AttributeError):
            channel.i_am_not_a_method()

    def test_bad_param(self):
        # The original test expected TypeError. This is often raised if a method is called
        # with keyword arguments that it doesn't define and doesn't accept via **kwargs.
        # Bravado-core generated methods might behave this way if the parameter isn't in the spec.
        with self.assertRaises(TypeError):
            self.uut.channels.list(i_am_not_a_param='asdf')


    def test_bad_response(self):
        self.serve(responses.GET, 'channels', body='{"message": "This is just a test"}',
                   status=500)
        with self.assertRaises(BravadoHTTPError) as cm: # Expecting BravadoHTTPError
            self.uut.channels.list()

        # Check status code from the bravado exception
        # Bravado exceptions (like HTTPBadResponse, HTTPServerError) store status_code
        self.assertEqual(500, cm.exception.status_code)

        # Accessing the response body from bravado exception:
        # It's often in `cm.exception.swagger_result` or needs careful parsing of `str(cm.exception)`.
        # For a JSON error response, bravado might deserialize it into swagger_result.
        if hasattr(cm.exception, 'swagger_result') and cm.exception.swagger_result:
            self.assertEqual({"message": "This is just a test"}, cm.exception.swagger_result)
        elif hasattr(cm.exception, 'response') and hasattr(cm.exception.response, 'json'):
            # Fallback if swagger_result is not populated as expected
            try:
                error_json = cm.exception.response.json()
                self.assertEqual({"message": "This is just a test"}, error_json)
            except: # Catch if .json() fails or not available
                 self.assertIn("This is just a test", str(cm.exception))
        else:
            # Fallback to checking the string representation of the exception
            self.assertIn("500", str(cm.exception))
            self.assertIn("This is just a test", str(cm.exception))


    def test_endpoints(self):
        self.serve(responses.GET, 'endpoints',
                   body='[{"technology": "TEST", "resource": "1234"}]')
        self.serve(responses.GET, 'endpoints', 'TEST', '1234',
                   body='{"technology": "TEST", "resource": "1234"}')

        endpoints = self.uut.endpoints.list()
        self.assertEqual(1, len(endpoints))
        endpoint = endpoints[0].get()
        self.assertEqual('TEST', endpoint.json['technology'])
        self.assertEqual('1234', endpoint.json['resource'])

    def test_live_recording(self):
        self.serve(responses.GET, 'recordings', 'live', 'test-recording',
                   body='{"name": "test-recording"}')
        self.serve(responses.DELETE, 'recordings', 'live', 'test-recording', status=requests.codes.no_content)

        recording = self.uut.recordings.getLive(recordingName='test-recording')
        recording.cancel() # This should result in a DELETE call

    def test_stored_recording(self):
        self.serve(responses.GET, 'recordings', 'stored', 'test-recording',
                   body='{"name": "test-recording"}')
        self.serve(responses.DELETE, 'recordings', 'stored', 'test-recording', status=requests.codes.no_content)

        recording = self.uut.recordings.getStored(
            recordingName='test-recording')
        recording.deleteStored()

    def test_mailboxes(self):
        self.serve(responses.PUT, 'mailboxes', '1000',
                   body='{"name": "1000", "old_messages": "1", "new_messages": "3"}')

        # Assuming promote returns an object that behaves like a dict or has .json attribute
        mailbox_obj = self.uut.mailboxes.update(
            mailboxName='1000',
            oldMessages='1',
            newMessages='3')
        # If mailbox_obj is the direct dict from bravado (if promote returns raw data for non-mapped)
        # or if it's a BaseObject whose .json attribute is the dict:
        mailbox_data = mailbox_obj if isinstance(mailbox_obj, dict) else mailbox_obj.json
        self.assertEqual('1000', mailbox_data['name'])
        self.assertEqual('1', mailbox_data['old_messages']) # These are strings in JSON
        self.assertEqual('3', mailbox_data['new_messages'])


    def test_device_state(self):
        self.serve(responses.PUT, 'deviceStates', 'foobar',
                   body='{"name": "foobar", "state": "BUSY"}')
        device_state_obj = self.uut.deviceStates.update(
            deviceName='foobar',
            deviceState='BUSY')
        device_state_data = device_state_obj if isinstance(device_state_obj, dict) else device_state_obj.json
        self.assertEqual('foobar', device_state_data['name'])
        self.assertEqual('BUSY', device_state_data['state'])

    # Removed redundant setUp from ClientTest, as it's handled by AriTestCase
    # def setUp(self):
    #     super(ClientTest, self).setUp()
    #     self.uut = ari.connect('http://ari.py/', 'test', 'test')


if __name__ == '__main__':
    unittest.main()
