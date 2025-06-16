#!/usr/bin/env python

"""WebSocket testing.
"""

import unittest
from unittest.mock import patch, MagicMock
import ari
# httpretty is no longer used here, responses is handled by AriTestCase
# from swaggerpy.http_client import SynchronousHttpClient # Not needed

from ari_test.utils import AriTestCase
import responses # For direct use like responses.DELETE if needed

# BASE_URL is defined in AriTestCase, can be used as self.BASE_URL
# No need for local GET, PUT, POST, DELETE aliases from httpretty


def raise_exceptions_handler(ex):
    """Testing exception handler for ARI client.
    To be used as client.exception_handler.
    """
    raise ex


# noinspection PyDocstring
class WebSocketTest(AriTestCase):
    def setUp(self):
        # super(WebSocketTest, self).setUp() will call responses_mock.start()
        # and self.uut = ari.connect('http://ari.py/', 'test', 'test')
        # For WebSocket tests, we often need a client not connected to the default WebSocket mock from setUp,
        # or we need to control the WebSocket messages specifically for each test.
        # We will re-initialize self.uut in each test or use a helper.
        super(WebSocketTest, self).setUp()
        self.actual = []
        # It's important that ari.Client uses the mocked websocket.create_connection
        # The self.uut from AriTestCase.setUp might not be suitable if its websocket isn't easily mockable post-init.
        # So, tests will create their own client instances with appropriate mocks.

    def record_event(self, event_or_data, event_obj_if_multi_arg=None):
        # Adapt to callbacks that might receive (obj, event) or just (event)
        if event_obj_if_multi_arg is not None: # (obj, event) style
            self.actual.append(event_obj_if_multi_arg)
        else: # (event) style
            self.actual.append(event_or_data)


    def create_mock_ws_client(self, messages):
        # Client for HTTP part of tests, self.uut from AriTestCase can be used if HTTP calls are needed.
        # For WebSocket, we mock websocket.create_connection.
        # The ari.Client instance should be created *after* the mock is in place.

        mock_ws = MagicMock()
        # Simulate recv(): pops from messages, then returns None to stop client loop
        mock_ws.recv.side_effect = messages + [None]
        mock_ws.send_close = MagicMock()
        mock_ws.close = MagicMock()
        return mock_ws

    @patch('ari.client.websocket.create_connection')
    def test_empty(self, mock_create_connection):
        mock_ws = self.create_mock_ws_client([])
        mock_create_connection.return_value = mock_ws

        # Use the base_url from AriTestCase or a specific one if needed
        client = ari.Client(self.BASE_URL, self.responses_mock) # Pass mock HTTP client
        client.exception_handler = raise_exceptions_handler
        client.on_event('ev', self.record_event)
        client.run(apps='test') # apps arg is important

        self.assertEqual([], self.actual)
        mock_create_connection.assert_called_once()
        self.assertTrue(mock_ws.recv.called) # Ensure recv was called
        self.assertTrue(mock_ws.close.called)


    @patch('ari.client.websocket.create_connection')
    def test_series(self, mock_create_connection):
        messages = [
            '{"type": "ev", "data": 1}',
            '{"type": "ev", "data": 2}',
            '{"type": "not_ev", "data": 3}',
            '{"type": "not_ev", "data": 5}',
            '{"type": "ev", "data": 9}'
        ]
        mock_ws = self.create_mock_ws_client(messages)
        mock_create_connection.return_value = mock_ws

        client = ari.Client(self.BASE_URL, self.responses_mock)
        client.exception_handler = raise_exceptions_handler
        client.on_event("ev", self.record_event)
        client.run(apps='test')

        expected = [
            {"type": "ev", "data": 1},
            {"type": "ev", "data": 2},
            {"type": "ev", "data": 9}
        ]
        self.assertEqual(expected, self.actual)

    @patch('ari.client.websocket.create_connection')
    def test_unsubscribe(self, mock_create_connection):
        messages = [
            '{"type": "ev", "data": 1}',
            '{"type": "ev", "data": 2}'
        ]
        mock_ws = self.create_mock_ws_client(messages)
        mock_create_connection.return_value = mock_ws

        client = ari.Client(self.BASE_URL, self.responses_mock)
        client.exception_handler = raise_exceptions_handler
        self.once_ran = 0

        def only_once(event): # event is the first arg
            self.once_ran += 1
            self.assertEqual(1, event['data'])
            self.once.close() # Assuming .close() on the unsubscriber object

        def both_events(event): # event is the first arg
            self.record_event(event)

        self.once = client.on_event("ev", only_once)
        self.both = client.on_event("ev", both_events)
        client.run(apps='test')

        expected = [
            {"type": "ev", "data": 1},
            {"type": "ev", "data": 2}
        ]
        self.assertEqual(expected, self.actual)
        self.assertEqual(1, self.once_ran)

    @patch('ari.client.websocket.create_connection')
    def test_on_channel(self, mock_create_connection):
        # This test also makes an HTTP DELETE call (channel.hangup())
        self.serve(responses.DELETE, 'channels', 'test-channel') # Setup for self.uut.channels.get().hangup()

        messages = [
            '{ "type": "StasisStart", "channel": { "id": "test-channel" } }'
        ]
        mock_ws = self.create_mock_ws_client(messages)
        mock_create_connection.return_value = mock_ws

        client = ari.Client(self.BASE_URL, self.responses_mock) # Use self.responses_mock for HTTP
        client.exception_handler = raise_exceptions_handler

        def cb(channel_obj, event): # obj, event
            self.record_event(event) # Record the event
            channel_obj.hangup()

        client.on_channel_event('StasisStart', cb)
        client.run(apps='test')

        expected = [
            {"type": "StasisStart", "channel": {"id": "test-channel"}}
        ]
        self.assertEqual(expected, self.actual)

    @patch('ari.client.websocket.create_connection')
    def test_on_channel_unsubscribe(self, mock_create_connection):
        messages = [
            '{ "type": "StasisStart", "channel": { "id": "test-channel1" } }',
            '{ "type": "StasisStart", "channel": { "id": "test-channel2" } }'
        ]
        mock_ws = self.create_mock_ws_client(messages)
        mock_create_connection.return_value = mock_ws

        client = ari.Client(self.BASE_URL, self.responses_mock)
        client.exception_handler = raise_exceptions_handler

        def only_once(channel_obj, event): # obj, event
            self.record_event(event)
            self.once.close()

        self.once = client.on_channel_event('StasisStart', only_once)
        client.run(apps='test')

        expected = [
            {"type": "StasisStart", "channel": {"id": "test-channel1"}}
        ]
        self.assertEqual(expected, self.actual)

    @patch('ari.client.websocket.create_connection')
    def test_channel_on_event(self, mock_create_connection):
        # HTTP calls setup
        self.serve(responses.GET, 'channels', 'test-channel', body='{"id": "test-channel", "name": "test-channel-name"}')
        self.serve(responses.DELETE, 'channels', 'test-channel')

        messages = [
            '{"type": "ChannelStateChange", "channel": {"id": "ignore-me"}}',
            '{"type": "ChannelStateChange", "channel": {"id": "test-channel"}}'
        ]
        mock_ws = self.create_mock_ws_client(messages)
        mock_create_connection.return_value = mock_ws

        # Use self.uut because it's already set up with responses_mock by AriTestCase
        # and its HTTP client is what self.serve mocks.
        # However, we need its websocket to be mocked.
        # So, it's better to create a new client here.
        client = ari.Client(self.BASE_URL, self.responses_mock)
        client.exception_handler = raise_exceptions_handler

        channel = client.channels.get(channelId='test-channel')

        def cb(channel_obj, event): # obj, event
            self.record_event(event)
            channel_obj.hangup()

        channel.on_event('ChannelStateChange', cb)
        client.run(apps='test')

        expected = [
            {"type": "ChannelStateChange", "channel": {"id": "test-channel"}}
        ]
        self.assertEqual(expected, self.actual)

    @patch('ari.client.websocket.create_connection')
    def test_arbitrary_callback_arguments(self, mock_create_connection):
        self.serve(responses.GET, 'channels', 'test-channel', body='{"id": "test-channel", "name": "test-name"}')
        self.serve(responses.DELETE, 'channels', 'test-channel')
        messages = [
            '{"type": "ChannelDtmfReceived", "channel": {"id": "test-channel"}}'
        ]
        obj_param = {'key': 'val'}
        mock_ws = self.create_mock_ws_client(messages)
        mock_create_connection.return_value = mock_ws

        client = ari.Client(self.BASE_URL, self.responses_mock)
        client.exception_handler = raise_exceptions_handler
        channel = client.channels.get(channelId='test-channel')

        def cb(channel_obj, event, arg_cb): # Renamed arg to arg_cb
            if arg_cb == 'done':
                channel_obj.hangup()
            else:
                self.record_event(arg_cb)

        def cb2(channel_obj, event, arg1, arg2=None, arg3=None):
            self.record_event(arg1)
            self.record_event(arg2)
            self.record_event(arg3)

        channel.on_event('ChannelDtmfReceived', cb, 1)
        channel.on_event('ChannelDtmfReceived', cb, arg_cb=2) # Pass by kwarg name
        channel.on_event('ChannelDtmfReceived', cb, obj_param)
        channel.on_event('ChannelDtmfReceived', cb2, 2.0, arg3=[1, 2, 3])
        channel.on_event('ChannelDtmfReceived', cb, 'done')
        client.run(apps='test')

        expected = [1, 2, obj_param, 2.0, None, [1, 2, 3]]
        self.assertEqual(expected, self.actual)

    # test_bad_event_type and test_bad_object_type use self.uut from AriTestCase
    # which is fine as they don't involve active websocket communication,
    # but rather the setup of event handlers.
    def test_bad_event_type(self):
        # self.uut is from AriTestCase, uses responses_mock for HTTP if any calls were made
        # This test checks logic in on_object_event before any websocket connection.
        with self.assertRaises(ValueError):
            self.uut.on_object_event(
                'BadEventType', self.noop, self.noop, 'Channel')

    def test_bad_object_type(self):
        with self.assertRaises(ValueError):
            self.uut.on_object_event('StasisStart', self.noop, self.noop, 'Bridge')

    def noop(self, *args, **kwargs): # Made it a method
        self.fail("Noop unexpectedly called")


# Removed WebSocketStubConnection and WebSocketStubClient as they are no longer used.
# Removed local connect() helper function. Tests now directly instantiate ari.Client with mocks.

if __name__ == '__main__':
    unittest.main()
