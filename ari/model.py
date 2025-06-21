#!/usr/bin/env python

"""Model for mapping ARI Swagger resources and operations into objects.

The API is modeled into the Repository pattern, as you would find in Domain
Driven Design.

Each Swagger Resource (a.k.a. API declaration) is mapped into a Repository
object, which has the non-instance specific operations (just like what you
would find in a repository object).

Responses from operations are mapped into first-class objects, which themselves
have methods which map to instance specific operations (just like what you
would find in a domain object).

The first-class objects also have 'on_event' methods, which can subscribe to
Stasis events relating to that object.
"""

import re
# import requests # Keep for requests.codes if needed, or remove if bravado provides alternatives
import logging
from bravado_core.exception import HTTPError # For type hinting if needed, bravado raises by default

log = logging.getLogger(__name__)


class Repository(object):
    """ARI repository.

    This repository maps to an ARI Swagger resource. The operations on the
    Swagger resource are mapped to methods on this object, using the
    operation's nickname.

    :param client:  ARI client.
    :type  client:  client.Client
    :param name:    Repository name. Maps to the basename of the resource's
                    .json file
    :param resource:    Associated bravado_core Resource object.
    :type  resource:    bravado_core.resource.Resource
    """

    def __init__(self, client, name, resource):
        self.client = client
        self.name = name
        self.bravado_resource = resource # Renamed from self.api to be clear

    def __repr__(self):
        return "Repository(%s)" % self.name

    def __getattr__(self, item):
        """Maps resource operations to methods on this object.

        :param item: Item name (operationId or nickname).
        """
        # getattr on a bravado_core.resource.Resource gives you the operation method
        bravado_operation_callable = getattr(self.bravado_resource, item, None)
        if not callable(bravado_operation_callable):
            raise AttributeError(
                "'%r' object has no attribute '%s' or it's not callable" % (self, item))

        # Access the operation spec from the callable (e.g., bravado_operation_callable.operation.op_spec)
        # This structure depends on bravado-core's internals for how it attaches spec to operation callables.
        # Typically, a bravado operation method has an 'operation' attribute which is an Operation object,
        # and that Operation object has an 'op_spec' attribute.
        if not hasattr(bravado_operation_callable, 'operation') or \
           not hasattr(bravado_operation_callable.operation, 'op_spec'):
            raise AttributeError(
                "Operation '%s' does not have expected spec structure" % item)

        operation_spec = bravado_operation_callable.operation.op_spec

        def new_callable(**kwargs):
            # Execute the bravado operation
            # .result() will raise an HTTPError for non-2XX responses by default
            http_future = bravado_operation_callable(**kwargs)
            bravado_response = http_future.result() # This is bravado_core.response.IncomingResponse
                                                 # or the deserialized object directly if configured.
                                                 # Assuming it's IncomingResponse which has .result for body.
            return promote(self.client, bravado_response, operation_spec)
        return new_callable


class ObjectIdGenerator(object):
    """Interface for extracting identifying information from an object's JSON
    representation.
    """

    def get_params(self, obj_json):
        """Gets the paramater values for specifying this object in a query.

        :param obj_json: Instance data.
        :type  obj_json: dict
        :return: Dictionary with paramater names and values
        :rtype:  dict of str, str
        """
        raise NotImplementedError("Not implemented")

    def id_as_str(self, obj_json):
        """Gets a single string identifying an object.

        :param obj_json: Instance data.
        :type  obj_json: dict
        :return: Id string.
        :rtype:  str
        """
        raise NotImplementedError("Not implemented")


# noinspection PyDocstring
class DefaultObjectIdGenerator(ObjectIdGenerator):
    """Id generator that works for most of our objects.

    :param param_name:  Name of the parameter to specify in queries.
    :param id_field:    Name of the field to specify in JSON.
    """

    def __init__(self, param_name, id_field='id'):
        self.param_name = param_name
        self.id_field = id_field

    def get_params(self, obj_json):
        return {self.param_name: obj_json[self.id_field]}

    def id_as_str(self, obj_json):
        return obj_json[self.id_field]


class BaseObject(object):
    """Base class for ARI domain objects.

    :param client:  ARI client.
    :type  client:  client.Client
    :param bravado_resource: Associated bravado_core.resource.Resource object.
    :type  bravado_resource: bravado_core.resource.Resource
    :param as_json: JSON representation of this object instance.
    :type  as_json: dict
    :param event_reg: Event registration callback.
    """

    id_generator = ObjectIdGenerator()

    def __init__(self, client, bravado_resource, as_json, event_reg):
        self.client = client
        self.bravado_resource = bravado_resource # Renamed from self.api
        self.json = as_json
        self.id = self.id_generator.id_as_str(as_json)
        self.event_reg = event_reg

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__, self.id)

    def __getattr__(self, item):
        """Promote resource operations related to a single resource to methods
        on this class.

        :param item: Item name (operationId or nickname).
        """
        bravado_operation_callable = getattr(self.bravado_resource, item, None)
        if not callable(bravado_operation_callable):
            raise AttributeError(
                "'%r' object has no attribute '%r' or it's not callable" % (self, item))

        if not hasattr(bravado_operation_callable, 'operation') or \
           not hasattr(bravado_operation_callable.operation, 'op_spec'):
            raise AttributeError(
                "Operation '%s' does not have expected spec structure" % item)

        operation_spec = bravado_operation_callable.operation.op_spec

        def enrich_operation(**kwargs):
            """Enriches an operation by specifying parameters specifying this
            object's id (i.e., channelId=self.id), and promotes HTTP response
            to a first-class object.

            :param kwargs: Operation parameters
            :return: First class object mapped from HTTP response.
            """
            # Add id to param list
            kwargs.update(self.id_generator.get_params(self.json))
            http_future = bravado_operation_callable(**kwargs)
            bravado_response = http_future.result()
            return promote(self.client, bravado_response, operation_spec)

        return enrich_operation

    def on_event(self, event_type, fn, *args, **kwargs):
        """Register event callbacks for this specific domain object.

        :param event_type: Type of event to register for.
        :type  event_type: str
        :param fn:  Callback function for events.
        :type  fn:  (object, dict) -> None
        :param args: Arguments to pass to fn
        :param kwargs: Keyword arguments to pass to fn
        """

        def fn_filter(objects, event, *args, **kwargs):
            """Filter received events for this object.

            :param objects: Objects found in this event.
            :param event: Event.
            """
            if isinstance(objects, dict):
                if self.id in [c.id for c in objects.values()]:
                    fn(objects, event, *args, **kwargs)
            else:
                if self.id == objects.id:
                    fn(objects, event, *args, **kwargs)

        if not self.event_reg:
            msg = "Event callback registration called on object with no events"
            raise RuntimeError(msg)

        return self.event_reg(event_type, fn_filter, *args, **kwargs)


class Channel(BaseObject):
    """First class object API.

    :param client:  ARI client.
    :type  client:  client.Client
    :param channel_json: Instance data
    """

    id_generator = DefaultObjectIdGenerator('channelId')

    def __init__(self, client, channel_json):
        super(Channel, self).__init__(
            client, client.swagger_client.channels, channel_json, # Use swagger_client
            client.on_channel_event)


class Bridge(BaseObject):
    """First class object API.

    :param client:  ARI client.
    :type  client:  client.Client
    :param bridge_json: Instance data
    """

    id_generator = DefaultObjectIdGenerator('bridgeId')

    def __init__(self, client, bridge_json):
        super(Bridge, self).__init__(
            client, client.swagger_client.bridges, bridge_json, # Use swagger_client
            client.on_bridge_event)


class Playback(BaseObject):
    """First class object API.

    :param client:  ARI client.
    :type  client:  client.Client
    :param playback_json: Instance data
    """
    id_generator = DefaultObjectIdGenerator('playbackId')

    def __init__(self, client, playback_json):
        super(Playback, self).__init__(
            client, client.swagger_client.playbacks, playback_json, # Use swagger_client
            client.on_playback_event)


class LiveRecording(BaseObject):
    """First class object API.

    :param client: ARI client
    :type  client: client.Client
    :param recording_json: Instance data
    """
    id_generator = DefaultObjectIdGenerator('recordingName', id_field='name')

    def __init__(self, client, recording_json):
        super(LiveRecording, self).__init__(
            client, client.swagger_client.recordings, recording_json, # Use swagger_client
            client.on_live_recording_event)


class StoredRecording(BaseObject):
    """First class object API.

    :param client: ARI client
    :type  client: client.Client
    :param recording_json: Instance data
    """
    id_generator = DefaultObjectIdGenerator('recordingName', id_field='name')

    def __init__(self, client, recording_json):
        super(StoredRecording, self).__init__(
            client, client.swagger_client.recordings, recording_json, # Use swagger_client
            client.on_stored_recording_event)


# noinspection PyDocstring
class EndpointIdGenerator(ObjectIdGenerator):
    """Id generator for endpoints, because they are weird.
    """

    def get_params(self, obj_json):
        return {
            'tech': obj_json['technology'],
            'resource': obj_json['resource']
        }

    def id_as_str(self, obj_json):
        return "%(tech)s/%(resource)s" % self.get_params(obj_json)


class Endpoint(BaseObject):
    """First class object API.

    :param client:  ARI client.
    :type  client:  client.Client
    :param endpoint_json: Instance data
    """
    id_generator = EndpointIdGenerator()

    def __init__(self, client, endpoint_json):
        super(Endpoint, self).__init__(
            client, client.swagger_client.endpoints, endpoint_json, # Use swagger_client
            client.on_endpoint_event)


class DeviceState(BaseObject):
    """First class object API.

    :param client:  ARI client.
    :type  client:  client.Client
    :param endpoint_json: Instance data
    """
    id_generator = DefaultObjectIdGenerator('deviceName', id_field='name')

    def __init__(self, client, device_state_json):
        super(DeviceState, self).__init__(
            client, client.swagger_client.deviceStates, device_state_json, # Use swagger_client
            client.on_device_state_event)


class Sound(BaseObject):
    """First class object API.

    :param client:  ARI client.
    :type  client:  client.Client
    :param sound_json: Instance data
    """

    id_generator = DefaultObjectIdGenerator('soundId')

    def __init__(self, client, sound_json):
        super(Sound, self).__init__(
            client, client.swagger_client.sounds, sound_json, client.on_sound_event) # Use swagger_client


class Mailbox(BaseObject):
    """First class object API.

    :param client:       ARI client.
    :type  client:       client.Client
    :param mailbox_json: Instance data
    """

    id_generator = DefaultObjectIdGenerator('mailboxName', id_field='name')

    def __init__(self, client, mailbox_json):
        super(Mailbox, self).__init__(
            client, client.swagger_client.mailboxes, mailbox_json, None) # Use swagger_client


def promote(client, bravado_response, operation_spec):
    """Promote a response from bravado_core to a first-class ARI object.

    :param client: ARI client.
    :type  client: ari.client.Client
    :param bravado_response: The response object from a bravado_core operation call.
                             This is typically bravado_core.response.IncomingResponse,
                             or can be the direct deserialized result if configured.
    :type bravado_response: bravado_core.response.IncomingResponse or dict/list/etc.
    :param operation_spec: bravado_core operation specification object.
    :type  operation_spec: bravado_core.spec.OpSpec
    :return: Promoted object or list of objects, or raw JSON data.
    """
    # bravado-core raises HTTPError for non-2XX by default when .result() is called on the future.
    # So, no need for bravado_response.raise_for_status() if we got here via .result().
    # If bravado_response is the direct result, then status code check is relevant for 204.

    status_code = None
    response_data = None

    if hasattr(bravado_response, 'status_code') and hasattr(bravado_response, 'result'):
        # It's likely an IncomingResponse wrapper
        status_code = bravado_response.status_code
        response_data = bravado_response.result # Deserialized body
    else:
        # It might be the direct deserialized result (e.g., for a 200 OK with body)
        # or None (e.g., for a 204 No Content if bravado is configured to return None directly)
        response_data = bravado_response
        # We need a status code if we are to check for 204.
        # This path is problematic if we need to distinguish 204 from an empty 200 response.
        # For now, assume if it's not an IncomingResponse, it's a successful result or None for 204.
        if response_data is None: # Potentially a 204
             # We can't be sure it was 204 without the status_code from IncomingResponse.
             # This logic might need to be revisited depending on bravado_core configuration.
             # For now, if data is None, assume it's like a 204.
             pass


    # Determine the expected response type from the operation_spec
    # Example: operation_spec.spec_dict['responses']['200']['schema']
    # This can be complex due to $ref, type, items, etc.

    # Default to string for response_class_name if not found, to avoid errors.
    response_class_name = "Unknown"
    is_list = False

    # Try to get schema for 200 or 201 response, typical success codes with bodies
    # Bravado should have already used this to deserialize into response_data
    success_schema = None
    if str(status_code) in operation_spec.spec_dict.get('responses', {}):
        success_schema = operation_spec.spec_dict['responses'][str(status_code)].get('schema')
    elif '200' in operation_spec.spec_dict.get('responses', {}): # Fallback to 200
        success_schema = operation_spec.spec_dict['responses']['200'].get('schema')
    elif 'default' in operation_spec.spec_dict.get('responses', {}): # Fallback to default
        success_schema = operation_spec.spec_dict['responses']['default'].get('schema')


    if success_schema:
        if success_schema.get('type') == 'array' and '$ref' in success_schema.get('items', {}):
            is_list = True
            ref_path = success_schema['items']['$ref']
            response_class_name = ref_path.split('/')[-1]  # Extract type name from $ref
        elif '$ref' in success_schema:
            is_list = False
            ref_path = success_schema['$ref']
            response_class_name = ref_path.split('/')[-1] # Extract type name from $ref
        elif 'type' in success_schema: # Primitive type, not a model usually
            response_class_name = success_schema['type']
            if response_class_name == 'array' and 'items' in success_schema and 'type' in success_schema['items']:
                 # Array of primitives, not mapped to custom ARI objects
                 pass # Keep response_data as is.

    factory = CLASS_MAP.get(response_class_name)

    # First, handle explicit 204 No Content if we have a status code
    if status_code == 204:
        return None

    if factory:
        if is_list:
            if isinstance(response_data, list):
                # Filter out any None items if the list might contain them,
                # though typically a list of objects shouldn't have None items unless spec allows.
                return [factory(client, obj_json) for obj_json in response_data if obj_json is not None]
            else:
                log.warning(f"Expected a list for {response_class_name} but got {type(response_data)}")
                # Depending on strictness, could raise error or return empty list/None
                return None
        else:
            # If a factory is found for a single object, but response_data is None
            # (and it wasn't a 204, e.g. empty 200 body for an optional object), return None.
            if response_data is None:
                return None
            return factory(client, response_data) # response_data should be a dict here

    # If no factory, but we have data, return the raw data.
    if response_data is not None:
        log.info("No ARI class mapping for type '%s'; returning raw data: %s", response_class_name, str(response_data)[:100])
        return response_data

    # Default fallback (e.g. response_data was None, not a 204, and no factory matched)
    return None


CLASS_MAP = {
    'Bridge': Bridge,
    'Channel': Channel,
    'Endpoint': Endpoint,
    'Playback': Playback,
    'LiveRecording': LiveRecording,
    'StoredRecording': StoredRecording,
    'Mailbox': Mailbox,
    'DeviceState': DeviceState,
}
