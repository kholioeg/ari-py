#
# Copyright (c) 2013, Digium, Inc.
#

"""ARI client library.
"""

import json
import logging
from urllib.parse import urljoin, urlparse
import websocket # Using websocket-client
from bravado_core.spec import Spec
from bravado_core.client import SwaggerClient
from bravado_core.http_client import RequestsClient # Default, or use the one passed in

from ari.model import *

log = logging.getLogger(__name__)


class Client(object):
    """ARI Client object.

    :param base_url: Base URL for accessing Asterisk.
    :param http_client: HTTP client interface.
    """

    def __init__(self, base_url, http_client_input): # Renamed http_client to http_client_input to avoid conflict
        # Ensure http_client_input is a bravado-core compatible HttpClient
        # If it's a requests.Session, bravado-core can use it directly.
        # If not, it might need wrapping or adjustment.
        # For now, assuming http_client_input is compatible (e.g. a requests.Session)
        # or can be adapted by bravado-core.

        # If http_client_input is a raw requests.Session, bravado-core will wrap it.
        # If it's already a bravado_core.http_client.HttpClient, it will use it.
        # Let's ensure it's the bravado type if we need to call methods like .close() on it later via swagger_spec.
        # However, bravado_core.client.SwaggerClient expects bravado_core.http_client.HttpClient.
        # The original http_client was likely a swaggerpy http_client.
        # We'll assume the passed http_client_input is a requests.Session or similar that bravado-core can handle.

        self.raw_http_client = http_client_input # Store the original http_client if needed

        api_docs_url = urljoin(base_url, "ari/api-docs/resources.json")

        # TODO: Determine if http_client_input needs to be wrapped in RequestsClient
        # or if bravado can consume it directly. For now, let bravado handle it.
        # If http_client_input is a requests.Session, bravado-core handles it.
        self.swagger_spec = Spec.from_url(api_docs_url, http_client=http_client_input)
        self.swagger_client = SwaggerClient(self.swagger_spec, http_client=http_client_input)

        self.repositories = {
            name: Repository(self, name, resource)
            for (name, resource) in self.swagger_spec.resources.items()
        }

        # Extract event models from the spec definitions
        # This is an approximation; actual event model identification might be more complex
        # depending on how ARI defines them in its OpenAPI spec.
        self.event_models = {
            name: model_spec.spec_dict
            for name, model_spec in self.swagger_spec.definitions.items()
            # Heuristic: ARI event models often end with 'Event' or are referenced by event resources.
            # A more robust method would be to inspect the 'events' resource if defined,
            # or look for a specific marker/tag in the model definitions.
            # For now, this is a broad filter and might need refinement.
            # The original code looked for models under an 'events' resource declaration.
        }
        # Attempt to refine event_models based on 'events' resource if it exists
        if 'events' in self.swagger_spec.resources:
            # This part is still speculative as bravado-core's resource object structure
            # for accessing associated models needs to be confirmed.
            # Assuming models specific to an 'events' resource might be found via its operations or definitions.
            # For now, the above general collection from spec.definitions will be used.
            # The key is that self.event_models should be a dict where keys are event type strings
            # and values are model definitions (dicts).
            pass


        self.websockets = set()
        self.event_listeners = {}
        self.exception_handler = \
            lambda ex: log.exception("Event listener threw exception")

    def __getattr__(self, item):
        """Exposes repositories as fields of the client.

        :param item: Field name
        """
        repo = self.get_repo(item)
        if not repo:
            raise AttributeError(
                "'%r' object has no attribute '%s'" % (self, item))
        return repo

    def close(self):
        """Close this ARI client.

        This method will close any currently open WebSockets, and close the
        underlying Swaggerclient.
        """
        for ws in list(self.websockets): # Iterate over a copy for safe removal
            try:
                ws.send_close() # websocket-client uses send_close() then close()
                ws.close()
            except Exception as e:
                log.warning(f"Error closing WebSocket: {e}")

        # Close the http_client if it has a close method (e.g., if it's a requests.Session)
        # bravado-core's SwaggerClient holds the http_client it uses.
        if hasattr(self.swagger_client.http_client, 'close'):
            try:
                self.swagger_client.http_client.close()
            except Exception as e:
                log.warning(f"Error closing swagger_client's http_client: {e}")
        elif hasattr(self.raw_http_client, 'close'): # Fallback to the raw client if different
            try:
                self.raw_http_client.close()
            except Exception as e:
                log.warning(f"Error closing raw_http_client: {e}")


    def get_repo(self, name):
        """Get a specific repo by name.

        :param name: Name of the repo to get
        :return: Repository, or None if not found.
        :rtype:  ari.model.Repository
        """
        return self.repositories.get(name)

    def __run(self, ws):
        """Drains all messages from a WebSocket, sending them to the client's
        listeners.

        :param ws: WebSocket to drain.
        """
        # TypeChecker false positive on iter(callable, sentinel) -> iterator
        # Fixed in plugin v3.0.1
        # noinspection PyTypeChecker
        for msg_str in iter(lambda: ws.recv(), None):
            msg_json = json.loads(msg_str)
            if not isinstance(msg_json, dict) or 'type' not in msg_json:
                log.error("Invalid event: %s" % msg_str)
                continue

            listeners = list(self.event_listeners.get(msg_json['type'], []))
            for listener in listeners:
                # noinspection PyBroadException
                try:
                    callback, args, kwargs = listener
                    args = args or ()
                    kwargs = kwargs or {}
                    callback(msg_json, *args, **kwargs)
                except Exception as e:
                    self.exception_handler(e)

    def run(self, apps):
        """Connect to the WebSocket and begin processing messages.

        This method will block until all messages have been received from the
        WebSocket, or until this client has been closed.

        :param apps: Application (or list of applications) to connect for
        :type  apps: str or list of str
        """
        if isinstance(apps, list):
            apps = ','.join(apps)

        # Construct WebSocket URL
        # Base URL for WebSocket needs to be derived from `base_url`
        parsed_b_url = urlparse(base_url) # base_url from Client.__init__
        ws_scheme = 'wss' if parsed_b_url.scheme == 'https' else 'ws'
        # Assuming ARI events are typically at <base_ari_path>/events/eventWebsocket
        # If base_url = "http://localhost:8088/ari", then ws_url = "ws://localhost:8088/ari/events/eventWebsocket"

        # The path for eventWebsocket operation.
        # In swaggerpy, self.swagger.events.eventWebsocket directly made the call.
        # We need to find this path from the spec or assume it.
        # Common practice for ARI is "/ari/events/eventWebsocket" if base_url points to http://host:port
        # or "/events/eventWebsocket" if base_url already includes /ari, like http://host:port/ari

        # Let's try to build from base_url and a known relative path for events.
        # If base_url is "http://localhost:8088/prefix" and ARI is at "/ari" under that,
        # and events are at "/ari/events/eventWebsocket"
        # A common way swagger tools resolve this is by having the full path in the spec.
        # For bravado-core, an operation object would have `op.path_name`.
        # `self.swagger_client.events.eventWebsocket` should be the operation.

        websocket_path_segment = "/events/eventWebsocket" # Relative to ARI's application root

        # Ensure base_url ends with a slash for proper joining if it's just host or host/path_prefix
        effective_base_url = parsed_b_url.path.rstrip('/')

        ws_url_path = effective_base_url + websocket_path_segment
        ws_full_url = f"{ws_scheme}://{parsed_b_url.netloc}{ws_url_path}?app={apps}"

        # TODO: Add api_key if required by ARI for WebSockets.
        # Example: ws_full_url += "&api_key=your_api_key"
        # This information would typically come from how http_client is configured (e.g. with auth).
        # swaggerpy might have handled this implicitly.

        log.info(f"Connecting to WebSocket: {ws_full_url}")
        ws = websocket.create_connection(ws_full_url)

        self.websockets.add(ws)
        try:
            self.__run(ws)
        finally:
            try:
                ws.close()
            except Exception as e:
                log.warning(f"Error during WebSocket close in finally block: {e}")
            if ws in self.websockets:
                self.websockets.remove(ws)

    def on_event(self, event_type, event_cb, *args, **kwargs):
        """Register callback for events with given type.

        :param event_type: String name of the event to register for.
        :param event_cb: Callback function
        :type  event_cb: (dict) -> None
        :param args: Arguments to pass to event_cb
        :param kwargs: Keyword arguments to pass to event_cb
        """
        listeners = self.event_listeners.setdefault(event_type, list())
        for cb in listeners:
            if event_cb == cb[0]:
                listeners.remove(cb)
        callback_obj = (event_cb, args, kwargs)
        listeners.append(callback_obj)
        client = self

        class EventUnsubscriber(object):
            """Class to allow events to be unsubscribed.
            """

            def close(self):
                """Unsubscribe the associated event callback.
                """
                if callback_obj in client.event_listeners[event_type]:
                    client.event_listeners[event_type].remove(callback_obj)

        return EventUnsubscriber()

    def on_object_event(self, event_type, event_cb, factory_fn, model_id,
                        *args, **kwargs):
        """Register callback for events with the given type. Event fields of
        the given model_id type are passed along to event_cb.

        If multiple fields of the event have the type model_id, a dict is
        passed mapping the field name to the model object.

        :param event_type: String name of the event to register for.
        :param event_cb: Callback function
        :type  event_cb: (Obj, dict) -> None or (dict[str, Obj], dict) ->
        :param factory_fn: Function for creating Obj from JSON
        :param model_id: String id for Obj from Swagger models.
        :param args: Arguments to pass to event_cb
        :param kwargs: Keyword arguments to pass to event_cb
        """
        # Find the associated model from the Swagger declaration (now bravado_core based)
        event_model_spec = self.event_models.get(event_type) # This now gets the raw spec_dict for the model
        if not event_model_spec:
            raise ValueError("Cannot find event model '%s'" % event_type)

        # Extract the fields that are of the expected type
        # The structure of event_model_spec (from bravado_core.spec.Spec.definitions[...].spec_dict)
        # should be similar to the old event_model structure.
        obj_fields = [k for (k, v) in event_model_spec.get('properties', {}).items()
                      if v.get('type') == model_id or (v.get('$ref') and v.get('$ref').endswith(f'/{model_id}'))]
        if not obj_fields:
            raise ValueError("Event model '%s' has no fields of type %s"
                             % (event_type, model_id))

        def extract_objects(event, *args, **kwargs):
            """Extract objects of a given type from an event.

            :param event: Event
            :param args: Arguments to pass to the event callback
            :param kwargs: Keyword arguments to pass to the event
                                      callback
            """
            # Extract the fields which are of the expected type
            obj = {obj_field: factory_fn(self, event[obj_field])
                   for obj_field in obj_fields
                   if event.get(obj_field)}
            # If there's only one field in the schema, just pass that along
            if len(obj_fields) == 1 and obj: # Ensure obj is not empty
                # obj is a dict, get its first value
                obj = next(iter(obj.values()))
            elif not obj: # If obj is empty (no matching fields found in the event instance)
                 obj = None
            event_cb(obj, event, *args, **kwargs)

        return self.on_event(event_type, extract_objects,
                             *args,
                             **kwargs)

    def on_channel_event(self, event_type, fn, *args, **kwargs):
        """Register callback for Channel related events

        :param event_type: String name of the event to register for.
        :param fn: Callback function
        :type  fn: (Channel, dict) -> None or (list[Channel], dict) -> None
        :param args: Arguments to pass to fn
        :param kwargs: Keyword arguments to pass to fn
        """
        return self.on_object_event(event_type, fn, Channel, 'Channel',
                                    *args, **kwargs)

    def on_bridge_event(self, event_type, fn, *args, **kwargs):
        """Register callback for Bridge related events

        :param event_type: String name of the event to register for.
        :param fn: Callback function
        :type  fn: (Bridge, dict) -> None or (list[Bridge], dict) -> None
        :param args: Arguments to pass to fn
        :param kwargs: Keyword arguments to pass to fn
        """
        return self.on_object_event(event_type, fn, Bridge, 'Bridge',
                                    *args, **kwargs)

    def on_playback_event(self, event_type, fn, *args, **kwargs):
        """Register callback for Playback related events

        :param event_type: String name of the event to register for.
        :param fn: Callback function
        :type  fn: (Playback, dict) -> None or (list[Playback], dict) -> None
        :param args: Arguments to pass to fn
        :param kwargs: Keyword arguments to pass to fn
        """
        return self.on_object_event(event_type, fn, Playback, 'Playback',
                                    *args, **kwargs)

    def on_live_recording_event(self, event_type, fn, *args, **kwargs):
        """Register callback for LiveRecording related events

        :param event_type: String name of the event to register for.
        :param fn: Callback function
        :type  fn: (LiveRecording, dict) -> None or (list[LiveRecording], dict) -> None
        :param args: Arguments to pass to fn
        :param kwargs: Keyword arguments to pass to fn
        """
        return self.on_object_event(event_type, fn, LiveRecording,
                                    'LiveRecording', *args, **kwargs)

    def on_stored_recording_event(self, event_type, fn, *args, **kwargs):
        """Register callback for StoredRecording related events

        :param event_type: String name of the event to register for.
        :param fn: Callback function
        :type  fn: (StoredRecording, dict) -> None or (list[StoredRecording], dict) -> None
        :param args: Arguments to pass to fn
        :param kwargs: Keyword arguments to pass to fn
        """
        return self.on_object_event(event_type, fn, StoredRecording,
                                    'StoredRecording', *args, **kwargs)

    def on_endpoint_event(self, event_type, fn, *args, **kwargs):
        """Register callback for Endpoint related events

        :param event_type: String name of the event to register for.
        :param fn: Callback function
        :type  fn: (Endpoint, dict) -> None or (list[Endpoint], dict) -> None
        :param args: Arguments to pass to fn
        :param kwargs: Keyword arguments to pass to fn
        """
        return self.on_object_event(event_type, fn, Endpoint, 'Endpoint',
                                    *args, **kwargs)

    def on_device_state_event(self, event_type, fn, *args, **kwargs):
        """Register callback for DeviceState related events

        :param event_type: String name of the event to register for.
        :param fn: Callback function
        :type  fn: (DeviceState, dict) -> None or (list[DeviceState], dict) -> None
        :param args: Arguments to pass to fn
        :param kwargs: Keyword arguments to pass to fn
        """
        return self.on_object_event(event_type, fn, DeviceState, 'DeviceState',
                                    *args, **kwargs)

    def on_sound_event(self, event_type, fn, *args, **kwargs):
        """Register callback for Sound related events

        :param event_type: String name of the event to register for.
        :param fn: Sound function
        :type  fn: (Sound, dict) -> None or (list[Sound], dict) -> None
        :param args: Arguments to pass to fn
        :param kwargs: Keyword arguments to pass to fn
        """
        return self.on_object_event(event_type, fn, Sound, 'Sound',
                                    *args, **kwargs)

