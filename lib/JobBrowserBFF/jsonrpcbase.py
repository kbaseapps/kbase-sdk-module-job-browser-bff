# -*- coding: utf-8 -*-

# The MIT License
#
# Copyright (c) 2010 Juhani Åhman <juhani.ahman@cs.helsinki.fi>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

#
# Note, this was copied from the jsonrpcbase project, an library which has
# been used at KBase for many years (in kb_sdk for example). It has not received
# updates in many years, and our way of using it is specific enough that
# it is useful to use the library for it's established and well-understood
# api while at the same time adding auth support.
#
# Removed dependency up on the 'six' library, since we are only using this with
# Python 3.
#
# We don't use the validation part of the library, so that has been deleted.
# TODO: It will be replaced with json-scheme based optional validation.
#
"""
Simple JSON-RPC service without transport layer

This library is intended as an auxiliary library for easy an implementation of JSON-RPC services
with Unix/TCP socket-like transport protocols that do not have complex special requirements. You
need to utilize some suitable transport protocol with this library to actually provide a working
JSON-RPC service.

Features:
- Easy to use, small size, well tested.
- Supports JSON-RPC v2.0. Compatible with v1.x style calls with the exception of v1.0
  class-hinting.
- Optional argument type validation that significantly eases development of jsonrpc method_data.

Example:

    import jsonrpcbase

    chat_service = jsonrpcbase.JSONRPCService()

    def login(username, password, timelimit=0):
        (...)
        return True

    def receive_message(**kwargs):
        (...)
        return chat_message

    def send_message(msg):
        (...)

    if __name__ == '__main__':

        # Adds the method login to the service as a 'login'.
        chat_service.add(login, types=[basestring, basestring, int])

        # Adds the method receive_message to the service as a 'recv_msg'.
        chat_service.add(receive_message, name='recv_msg', types={"msg": basestring, "id": int})

        # Adds the method send_message as a 'send_msg' to the service.
        chat_service.add(send_message, 'send_msg')

        (...)

        # Receive a JSON-RPC call.
        jsonmsg = my_socket.recv()

        # Process the JSON-RPC call.
        result = chat_service.call(jsonmsg)

        # Send back results.
        my_socket.send(result)
"""
import json
import logging
import types

# import validation.validation as validation
from typing import Optional


DEFAULT_JSONRPC = "2.0"

log = logging.getLogger(__name__)


class JSONRPCService(object):
    """
    The JSONRPCService class is a JSON-RPC
    """

    def __init__(self, options={}):
        self.method_data = {}

        # self.validation = validation.Validation(load_schemas=True)

    def add(self, f, name=None):
        """
        Adds a new method to the jsonrpc service.

        Arguments:
        f -- the remote function
        name -- name of the method in the jsonrpc service

        If name argument is not given, function's own name will be used.
        """
        if name is None:
            fname = f.__name__  # Register the function using its own name.
        else:
            fname = name

        self.method_data[fname] = {"method": f}

    def call(self, jsondata, auth=None):
        """
        Calls jsonrpc service's method and returns its return value in a JSON string or None
        if there is none.

        Arguments:
        jsondata -- remote method call in jsonrpc format
        """
        result = self.call_py(jsondata, auth)
        if result is not None:
            return json.dumps(result)

        return None

    def call_py(self, jsondata, auth):
        """
        Calls jsonrpc service's method and returns its return value in python object format or
        None if there is none.

        This method is same as call() except the return value is a python object instead of
        JSON string. This method is mainly only useful for debugging purposes.
        """
        try:
            try:
                rdata = json.loads(jsondata)
            except ValueError:
                raise ParseError
        except ParseError as e:
            return self._get_err(e)

        # set some default values for error handling
        request = self._get_default_vals()

        try:
            if isinstance(rdata, dict) and rdata:
                # It's a single request.
                self._fill_request(request, rdata)
                respond = self._handle_request(request, auth)

                # Don't respond to notifications
                if respond is None:
                    return None

                return respond
            elif isinstance(rdata, list) and rdata:
                # It's a batch.
                requests = []
                responds = []

                for rdata_ in rdata:
                    # set some default values for error handling
                    request_ = self._get_default_vals()
                    try:
                        self._fill_request(request_, rdata_)
                    except InvalidRequestError as e:
                        err = self._get_err(e, request_["id"])
                        if err:
                            responds.append(err)
                        continue
                    except JSONRPCError as e:
                        err = self._get_err(e, request_["id"])
                        if err:
                            responds.append(err)
                        continue

                    requests.append(request_)

                for request_ in requests:
                    try:
                        respond = self._handle_request(request_, auth)
                    except JSONRPCError as e:
                        respond = self._get_err(e, request_["id"], request_["jsonrpc"])

                    # Don't respond to notifications
                    if respond is not None:
                        responds.append(respond)

                if responds:
                    return responds

                # Nothing to respond.
                return None
            else:
                # empty dict, list or wrong type
                raise InvalidRequestError

        except InvalidRequestError as e:
            return self._get_err(e, request["id"])
        except JSONRPCError as e:
            return self._get_err(e, request["id"], request["jsonrpc"])

    def _get_err(self, e, id=None, jsonrpc=DEFAULT_JSONRPC):
        """
        Returns jsonrpc error message.
        """
        # Do not respond to notifications when the request is valid.
        if (
            not id
            and not isinstance(e, ParseError)
            and not isinstance(e, InvalidRequestError)
        ):
            return None

        respond = {"id": id}

        if isinstance(jsonrpc, int):
            # v1.0 requires result to exist always.
            # No error codes are defined in v1.0 so only use the message.
            if jsonrpc == 10:
                respond["result"] = None
                respond["error"] = e.dumps()["message"]
            else:
                self._fill_ver(jsonrpc, respond)
                respond["error"] = e.dumps()
        else:
            respond["jsonrpc"] = jsonrpc
            respond["error"] = e.dumps()

        return respond

    def _fill_ver(self, iver, respond):
        """
        Fills version information to the respond from the internal integer version.
        """
        if iver == 20:
            respond["jsonrpc"] = "2.0"
        if iver == 11:
            respond["version"] = "1.1"

    def _vargs(self, f):
        """
        Returns True if given function accepts variadic positional arguments, otherwise False.
        """
        if f.__code__.co_flags & 4:
            return True

        return False

    def _man_args(self, f):
        """
        Returns number of mandatory arguments required by given function.
        """
        argcount = f.__code__.co_argcount

        # account for "self" getting passed to class instance methods
        if isinstance(f, types.MethodType):
            argcount -= 1

        if f.__defaults__ is None:
            return argcount

        return argcount - len(f.__defaults__)

    def _max_args(self, f):
        """
        Returns maximum number of arguments accepted by given function.
        """
        if f.__defaults__ is None:
            return f.__code__.co_argcount

        return f.__code__.co_argcount + len(f.__defaults__)

    def _get_jsonrpc(self, rdata):
        """
        Returns jsonrpc request's jsonrpc value.

        InvalidRequestError will be raised if the jsonrpc value has invalid value.
        """
        if "jsonrpc" in rdata:
            if rdata["jsonrpc"] == "2.0":
                return 20
            else:
                # invalid version
                raise InvalidRequestError
        else:
            # It's probably a JSON-RPC v1.x style call.
            if "version" in rdata:
                if rdata["version"] == "1.1":
                    return 11

        # Assume v1.0.
        return 10

    def _get_id(self, rdata):
        """
        Returns jsonrpc request's id value or None if there is none.

        InvalidRequestError will be raised if the id value has invalid type.
        """
        id = rdata.get("id")
        if not (
            isinstance(id, str)
            or isinstance(id, int)
            or isinstance(id, float)
            or id is None
        ):
            raise InvalidRequestError
        return id

    def _get_method(self, rdata):
        """
        Returns jsonrpc request's method value.

        InvalidRequestError will be raised if it's missing or is wrong type.
        MethodNotFoundError will be raised if a method with given method name does not exist.
        """
        if "method" in rdata:
            if not isinstance(rdata["method"], str):
                raise InvalidRequestError
        else:
            raise InvalidRequestError

        if rdata["method"] not in self.method_data.keys():
            raise MethodNotFoundError

        return rdata["method"]

    def _get_params(self, rdata):
        """
        Returns a list of jsonrpc request's method parameters.
        """
        if (
            isinstance(rdata["params"], dict)
            or isinstance(rdata["params"], list)
            or rdata["params"] is None
        ):
            return rdata["params"]
        else:
            # wrong type
            raise InvalidRequestError

    def _fill_request(self, request, rdata):
        """Fills request with data from the jsonrpc call."""
        if not isinstance(rdata, dict):
            raise InvalidRequestError

        request["jsonrpc"] = self._get_jsonrpc(rdata)
        request["id"] = self._get_id(rdata)
        request["method"] = self._get_method(rdata)
        if "params" in rdata:
            request["params"] = self._get_params(rdata)

    def _call_method(self, request, auth):
        """Calls given method with given params and returns it value."""
        method_name = request["method"]
        method = self.method_data[method_name]["method"]
        try:
            if "params" in request and request["params"] is not None:
                if request["jsonrpc"] == 11:
                    # KBase (nearly always) uses just the first argument.
                    params = request["params"][0]
                else:
                    params = request["params"]

                # KBase hack alert
                # [module_name, function_name] = method_name.split('.')
                # if self.validation.has_param_validation(method_name):
                #     self.validation.validate_params(method_name, params)

                result = method(params, auth)

                # if self.validation.has_result_validation(method_name):
                #     self.validation.validate_result(method_name, result)
            else:
                result = method(auth)

            if request["jsonrpc"] == 11:
                return [result]
            else:
                return result
        except JSONRPCError:
            raise
        except Exception:
            log.exception("method %s threw an exception" % request["method"])
            # Exception was raised inside the method.
            raise ServerError

    def _handle_request(self, request, auth):
        """Handles given request and returns its response."""
        result = self._call_method(request, auth)

        # Do not respond to notifications.
        if request["id"] is None:
            return None

        respond = {}
        self._fill_ver(request["jsonrpc"], respond)
        respond["result"] = result
        respond["id"] = request["id"]

        return respond

    def _get_default_vals(self):
        """
        Returns dictionary containing default jsonrpc request/responds values for
        error handling purposes.
        """
        return {"jsonrpc": DEFAULT_JSONRPC, "id": None}


class JSONRPCError(Exception):
    """
    JSONRPCError class based on the JSON-RPC 2.0 specs.

    code - number
    message - string
    data - object
    """

    code: int = 0
    message: Optional[str] = None
    data: Optional[dict] = None

    def __init__(self, message=None):
        """Setup the Exception and overwrite the default message."""
        if message is not None:
            self.message = message

    def dumps(self):
        """Return the Exception data in a format for JSON-RPC."""

        error = {"code": self.code, "message": str(self.message)}

        if self.data is not None:
            error["data"] = self.data

        return error


# ===============================================================================
# Exceptions
#
# The error-codes -32768 .. -32000 (inclusive) are reserved for pre-defined
# errors.
#
# Any error-code within this range not defined explicitly below is reserved
# for future use
# ===============================================================================


class ParseError(JSONRPCError):
    """Invalid JSON. An error occurred on the server while parsing the JSON text."""

    code = -32700
    message = "Parse error"


class InvalidRequestError(JSONRPCError):
    """The received JSON is not a valid JSON-RPC Request."""

    code = -32600
    message = "Invalid request"


class MethodNotFoundError(JSONRPCError):
    """The requested remote-procedure does not exist / is not available."""

    code = -32601
    message = "Method not found"


class InvalidParamsError(JSONRPCError):
    """Invalid method parameters."""

    code = -32602
    message = "Invalid params"

    def __init__(self, data=None):
        self.data = data


class InvalidResultError(JSONRPCError):
    """Invalid method result."""

    code = -32602
    message = "Invalid result"

    def __init__(self, data=None):
        self.data = data


class InternalError(JSONRPCError):
    """Internal JSON-RPC error."""

    code = -32603
    message = "Internal error"


# -32099..-32000 Server error. Reserved for implementation-defined server-errors.


class KeywordError(JSONRPCError):
    """The received JSON-RPC request is trying to use keyword arguments even though its
    version is 1.0."""

    code = -32099
    message = "Keyword argument error"


class ServerError(JSONRPCError):
    """Generic server error."""

    code = -32000
    message = "Server error"
