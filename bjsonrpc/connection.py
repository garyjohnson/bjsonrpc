"""
    bjson/connection.py
    
    Asynchronous Bidirectional JSON-RPC protocol implementation over TCP/IP
    
    Copyright (c) 2010 David Martinez Marti
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions
    are met:
    1. Redistributions of source code must retain the above copyright
       notice, this list of conditions and the following disclaimer.
    2. Redistributions in binary form must reproduce the above copyright
       notice, this list of conditions and the following disclaimer in the
       documentation and/or other materials provided with the distribution.
    3. Neither the name of copyright holders nor the names of its
       contributors may be used to endorse or promote products derived
       from this software without specific prior written permission.

    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
    ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
    TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
    PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL COPYRIGHT HOLDERS OR CONTRIBUTORS
    BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
    CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
    SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
    INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
    CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
    ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
    POSSIBILITY OF SUCH DAMAGE.

"""
# Local changes: 

import errno
import logging
import inspect
import socket, traceback, sys, threading
from types import MethodType, FunctionType

from bjsonrpc.proxies import Proxy
from bjsonrpc.request import Request
from bjsonrpc.exceptions import EofError, ServerError
from bjsonrpc import bjsonrpc_options

import bjsonrpc.jsonlib as json
import select


_log = logging.getLogger(__name__)
_log.setLevel(40)


class RemoteObject(object):
    """
        Represents a object in the server-side (or client-side when speaking from
        the point of view of the server) . It remembers its name in the server-side
        to allow calls to the original object.
        
        Parameters:
        
        **conn**
            Connection object which holds the socket to the other end 
            of the communications
        
        **obj**
            JSON object (Python dictionary) holding the values recieved.
            It is used to retrieve the properties to create the remote object.
            (Initially only used to get object name)
            
        Example::
        
            list = conn.call.newList()
            for i in range(10): list.notify.add(i)
            
            print list.call.getitems()
        
        Attributes:
        
        **name**
            name of the object in the server-side
        
        **call**
            Synchronous Proxy. It forwards your calls to it to the other end, waits
            the response and returns the value.

        **method**
            Asynchronous Proxy. It forwards your calls to it to the other end and
            inmediatelly returns a *request.Request* instance.

        **pipe**
            Asynchronous Proxy for "pipe" calls with multiple returns, like
            method but you can check request.value multiple times, and must
            call request.close() when you're done.

        **notify**
            Notification Proxy. It forwards your calls to it to the other end and
            tells the server to not response even if there's any error in the call.
            Returns *None*.
        
        
    """
    
    name = None 
    call = None 
    method = None 
    notify = None
    pipe = None

    def async(self, callback):
        return Proxy(self._conn, obj=self.name, sync_type=1, callback=callback)
    
    @property
    def connection(self): 
        """
            Public property to get the internal connection object.
        """
        return self._conn
    
    def __init__(self, conn, obj):
        self._conn = conn
        self.name = obj['__remoteobject__']
        
        self.call = Proxy(self._conn, obj=self.name, sync_type=0)
        self.method = Proxy(self._conn, obj=self.name, sync_type=1)
        self.notify = Proxy(self._conn, obj=self.name, sync_type=2)
        self.pipe = Proxy(self._conn, obj=self.name, sync_type=3)
    
    def __del__(self):
        self._close()
        
    def _close(self):
        """
            Internal close method called both by __del__() and public 
            method close()
        """
        self.call.__delete__()
        self.name = None
        
    def close(self):
        """
            Closes/deletes the remote object. The server may or may not delete
            it at this time, but after this call we don't longer have any access to it.
            
            This method is automatically called when Python deletes this instance.
        """
        return self._close()
        
        

class Connection(object): # TODO: Split this class in simple ones
    """ 
        Represents a communiation tunnel between two parties.
        
        **sck**
            Connected socket to use. Should be an instance of *socket.socket* or
            something compatible.
        
        **address**
            Address of the other peer in (host,port) form. It is only used to 
            inform handlers about the peer address.
        
        **handler_factory**
            Class type inherited from BaseHandler which holds the public methods.
            It defaults to *NullHandler* meaning no public methods will be 
            avaliable to the other end.

        **Members:**

        **call** 
            Synchronous Proxy. It forwards your calls to it to the other end, waits
            the response and returns the value
        
        **method**
            Asynchronous Proxy. It forwards your calls to it to the other end and
            inmediatelly returns a *request.Request* instance.
    
        **notify**
            Notification Proxy. It forwards your calls to it to the other end and
            tells the server to not response even if there's any error in the call.
            Returns *None*.
        
    """
    _maxtimeout = {
        'read' : 60,    # default maximum read timeout.
        'write' : 60,   # default maximum write timeout.
    }
    
    _SOCKET_COMM_ERRORS = (errno.ECONNABORTED, errno.ECONNREFUSED, 
                        errno.ECONNRESET, errno.ENETDOWN,
                        errno.ENETRESET, errno.ENETUNREACH)

    call = None 
    method = None 
    notify = None
    pipe = None
    
    @classmethod
    def setmaxtimeout(cls, operation, value):
        """
            Set the maximum timeout in seconds for **operation** operation.
            
            Parameters:
            
            **operation**
                The operation which has to be configured. Can be either 'read'
                or 'write'.
            
            **value**
                The timeout in seconds as a floating number. If is None, will 
                block until succeed. If is 0, will be nonblocking.
            
        """
        assert(operation in ['read', 'write'])
        cls._maxtimeout[operation] = value
    
    @classmethod
    def getmaxtimeout(cls, operation):
        """
            Get the maximum timeout in seconds for **operation** operation.
            
            Parameters:
            
            **operation**
                The operation which has to be configured. Can be either 'read'
                or 'write'.
            
            **(return value)**
                The timeout in seconds as a floating number or None.
            
        """
        if operation not in cls._maxtimeout: 
            return None
            
        return cls._maxtimeout[operation]
    
    
    def __init__(self, sck, address = None, handler_factory = None):
        self._debug_socket = False
        self._debug_dispatch = False
        self._buffer = b''
        self._sck = sck
        self._address = address
        self._handler = handler_factory 
        self.connection_status = "open"
        if self._handler: 
            self.handler = self._handler(self)
            
        self._id = 0
        self._requests = {}
        self._objects = {}

        self.scklock = threading.Lock()
        self.call = Proxy(self, sync_type=0)
        self.method = Proxy(self, sync_type=1)
        self.notify = Proxy(self, sync_type=2)
        self.pipe = Proxy(self, sync_type=3)
        self._wbuffer = b''
        self.write_lock = threading.RLock()
        self.read_lock = threading.RLock()
        self.getid_lock = threading.Lock()
        self.reading_event = threading.Event()
        self.threaded = bjsonrpc_options['threaded']
        self.write_thread_queue = []
        self.write_thread_semaphore = threading.Semaphore(0)
        self.write_thread = threading.Thread(target=self.write_thread)
        self.write_thread.daemon = True
        self.write_thread.start()

    @property
    def socket(self): 
        """
            public property that holds the internal socket used.
        """
        return self._sck
        
    def get_id(self):
        """
            Retrieves a new ID counter. Each connection has a exclusive ID counter.
            
            It is mainly used to create internal id's for calls.
        """
        self.getid_lock.acquire() 
        # Prevent two threads to execute this code simultaneously
        self._id += 1
        ret = self._id 
        self.getid_lock.release()
        
        return ret
        
    def load_object(self, obj):
        """
            Helper function for JSON loads. Given a dictionary (javascript object) returns
            an apropiate object (a specific class) in certain cases.
            
            It is mainly used to convert JSON hinted classes back to real classes.
            
            Parameters:
            
            **obj**
                Dictionary-like object to test.
                
            **(return value)**
                Either the same dictionary, or a class representing that object.
        """
        
        if '__remoteobject__' in obj: 
            return RemoteObject(self, obj)
            
        if '__objectreference__' in obj: 
            return self._objects[obj['__objectreference__']]
            
        if '__functionreference__' in obj:
            name = obj['__functionreference__']
            if '.' in name:
                objname, methodname = name.split('.')
                obj = self._objects[objname]
            else:
                obj = self.handler
                methodname = name
            method = obj.get_method(methodname)
            return method
        
        return obj
        
    def addrequest(self, request):
        """
            Adds a request to the queue of requests waiting for response.
        """
        assert(isinstance(request, Request))
        assert(request.request_id not in self._requests)
        self._requests[request.request_id] = request

    def delrequest(self, req_id):
        """
            Removes a request to the queue of requests waiting for response.
        """
        del self._requests[req_id]

    def dump_object(self, obj):
        """
            Helper function to convert classes and functions to JSON objects.
            
            Given a incompatible object called *obj*, dump_object returns a 
            JSON hinted object that represents the original parameter.
            
            Parameters:
            
            **obj**
                Object, class, function,etc which is incompatible with JSON 
                serialization.
                
            **(return value)**
                A valid serialization for that object using JSON class hinting.
                
        """
        # object of unknown type
        if type(obj) is FunctionType or type(obj) is MethodType :
            conn = getattr(obj, '_conn', None)
            if conn != self: 
                raise TypeError("Tried to serialize as JSON a handler for "
                "another connection!")
            return self._dump_functionreference(obj)
            
        if not isinstance(obj, object): 
            raise TypeError("JSON objects must be new-style classes")
            
        if not hasattr(obj, '__class__'): 
            raise TypeError("JSON objects must be instances, not types")
            
        if obj.__class__.__name__ == 'Decimal': # Probably is just a float.
            return float(obj)
            
        if isinstance(obj, RemoteObject): 
            return self._dump_objectreference(obj)
            
        if hasattr(obj, 'get_method'): 
            return self._dump_remoteobject(obj)
            
        raise TypeError("Python object %s laks a 'get_method' and "
            "is not serializable!" % repr(obj))

    def _dump_functionreference(self, obj):
        """ Converts obj to a JSON hinted-class functionreference"""
        return { '__functionreference__' : obj.__name__ }

    def _dump_objectreference(self, obj):
        """ Converts obj to a JSON hinted-class objectreference"""
        return { '__objectreference__' : obj.name }
        
    def _dump_remoteobject(self, obj):
        """ 
            Converts obj to a JSON hinted-class remoteobject, creating
            a RemoteObject if necessary
        """
        
        # An object can be remotely called if :
        #  - it derives from object (new-style classes)
        #  - it is an instance
        #  - has an internal function _get_method to handle remote calls
        if not hasattr(obj, '__remoteobjects__'): 
            obj.__remoteobjects__ = {}
            
        if self in obj.__remoteobjects__:
            instancename = obj.__remoteobjects__[self] 
        else:
            classname = obj.__class__.__name__
            instancename = "%s_%04x" % (classname.lower(), self.get_id())
            self._objects[instancename] = obj
            obj.__remoteobjects__[self] = instancename
        return { '__remoteobject__' : instancename }

    def _format_exception(self, obj, method, args, kw, exc):
        etype, evalue, etb = exc
        funargs = ", ".join(
            [repr(x) for x in args] +  
            ["%s=%r" % (k, kw[k]) for k in kw]
            )
        if len(funargs) > 40: 
            funargs = funargs[:37] + "..."
                
        _log.error("(%s) In Handler method %s.%s(%s) ",
                   obj.__class__.__module__,
                   obj.__class__.__name__,
                   method, 
                   funargs
            )
        _log.debug("\n".join([ "%s::%s:%d %s" % (
            filename, fnname, 
            lineno, srcline)
            for filename, lineno, fnname, srcline 
            in traceback.extract_tb(etb)[1:] ]))
        _log.error("Unhandled error: %s: %s", etype.__name__, evalue)
        del etb
        return '%s: %s' % (etype.__name__, evalue)

    def _dispatch_delete(self, objectname):
        try:
            self._objects[objectname]._shutdown()
        except Exception:
            _log.error("Error when shutting down the object %s:",
                       type(self._objects[objectname]))
            _log.debug(traceback.format_exc())
        del self._objects[objectname]

    def _extract_params(self, request):
        req_method = request.get("method")
        req_args = request.get("params", [])
        if type(req_args) is dict: 
            req_kwargs = req_args
            req_args = []
        else:
            req_kwargs = request.get("kwparams", {})
        if req_kwargs: 
            req_kwargs = dict((str(k), req_kwargs[k]) for k in req_kwargs)
        return req_method, req_args, req_kwargs
        
    def _find_object(self, req_method, req_args, req_kwargs):
        if '.' in req_method: # local-object.
            objectname, req_method = req_method.split('.')[:2]
            if objectname not in self._objects: 
                raise ValueError("Invalid object identifier")
            elif req_method == '__delete__':
                self._dispatch_delete(objectname)
            else:
                return self._objects[objectname]
        else:
            return self.handler
        
    def _find_method(self, req_object, req_method, req_args, req_kwargs):
        """
            Finds the method to process one request.
        """
        try:
            req_function = req_object.get_method(req_method)
            return req_function
        except ServerError as err:
            return str(err)
        except Exception:
            err = self._format_exception(req_object, req_method,
                                         req_args, req_kwargs,
                                         sys.exc_info())
            return err

    def dispatch_until_empty(self):
        """
            Calls *read_and_dispatch* method until there are no more messages to
            dispatch in the buffer.
            
            Returns the number of operations that succeded.
            
            This method will never block waiting. If there aren't 
            any more messages that can be processed, it returns.
        """
        ready_to_read = select.select( 
                    [self._sck], # read
                    [], [], # write, errors
                    0 # timeout
                    )[0]
                    
        if not ready_to_read: return 0
            
        newline_idx = 0
        count = 0
        while newline_idx != -1:
            if not self.read_and_dispatch(timeout=0): 
                break
            count += 1
            newline_idx = self._buffer.find(b'\n')
        return count
            
    def read_and_dispatch(self, timeout=None, thread=True, condition=None):
        """
            Read one message from socket (with timeout specified by the optional 
            argument *timeout*) and dispatches that message.
            
            Parameters:
            
            **timeout** = None
                Timeout in seconds of the read operation. If it is None 
                (or ommitted) then the read will wait 
                until new data is available.
                
            **(return value)**
                True, in case of the operation has suceeded and **one** message
                has been dispatched. False, if no data or malformed data has 
                been received.
                
        """
        self.read_lock.acquire()
        self.reading_event.set()
        try:
            if condition:
                if condition() == False:
                    return False
            if thread:
                dispatch_item = self.dispatch_item_threaded
            else:
                dispatch_item = self.dispatch_item_single
            
            data = self.read(timeout=timeout)
            if not data: 
                return False 
            try:
                item = json.loads(data, self)
                if type(item) is list: # batch call
                    for i in item: 
                        dispatch_item(i)
                elif type(item) is dict: # std call
                    if 'result' in item:
                        self.dispatch_item_single(item)
                    else:
                        dispatch_item(item)
                else: # Unknown format :-(
                    _log.debug("Received message with unknown format type: %s" , type(item))
                    return False
            except Exception:
                _log.debug(traceback.format_exc())
                return False
            return True
        finally:
            self.reading_event.clear()
            self.read_lock.release()
            
    def dispatch_item_threaded(self, item):
        """
            If threaded mode is activated, this function creates a new thread per
            each item received and returns without blocking.
        """
        if self.threaded:
            th1 = threading.Thread(target = self.dispatch_item_single, args = [ item ] )
            th1.start()
            return True
        else:
            return self.dispatch_item_single(item)
        
    def _send(self, response):
        txtResponse = None
        try:
            txtResponse = json.dumps(response, self)
        except Exception as e:
            _log.error("An unexpected error ocurred when trying to create the message: %r", e)
            response = {
                'result': None,
                'error': "InternalServerError: " + repr(e),
                'id': item['id']
                }
            txtResponse = json.dumps(response, self)
        try:
            self.write(txtResponse)
        except TypeError:
            _log.debug("response was: %r", response)
            raise

    def _send_response(self, item, response):
        if item.get('id') is not None:
            ret = { 'result': response, 'error': None, 'id': item['id'] }
            self._send(ret)

    def _send_error(self, item, err):
        if item.get('id') is not None:
            ret = { 'result': None, 'error': err, 'id': item['id'] }
            self._send(ret)

    def dispatch_item_single(self, item):
        """
            Given a JSON item received from socket, determine its type and 
            process the message.
        """
        assert(type(item) is dict)
        item.setdefault('id', None)
        
        if 'method' in item:
            method, args, kw = self._extract_params(item)
            obj = self._find_object(method, args, kw)
            if obj is None: return
            fn = self._find_method(obj, method, args, kw)
            try:
                if inspect.isgeneratorfunction(fn):
                    for response in fn(*args, **kw):
                        self._send_response(item, response)
                elif callable(fn):
                    self._send_response(item, fn(*args, **kw))
                elif fn:
                    self._send_error(item, fn)
            except ServerError as exc:
                self._send_error(item, str(exc))
            except Exception:
                err = self._format_exception(obj, method, args, kw,
                                             sys.exc_info())
                self._send_error(item, err)
        elif 'result' in item:
            assert(item['id'] in self._requests)
            request = self._requests[item['id']]
            request.setresponse(item)
        else:
            self._send_error(item, 'Unknown format')
        return True
    
    def proxy(self, sync_type, name, args, kwargs, callback = None):
        """
        Call method on server.

        sync_type :: 
          = 0 .. call method, wait, get response.
          = 1 .. call method, inmediate return of object.
          = 2 .. call notification and exit.
          = 3 .. call method, inmediate return of non-auto-close object.
          
        """
       
        data = {}
        data['method'] = name

        if sync_type in [0, 1, 3]: 
            data['id'] = self.get_id()
            
        if len(args) > 0: 
            data['params'] = args
            
        if len(kwargs) > 0: 
            if len(args) == 0: 
                data['params'] = kwargs
            else: 
                data['kwparams'] = kwargs
            
        if sync_type == 2: # short-circuit for speed!
            self.write(json.dumps(data, self))
            return None
                    
        req = Request(self, data, callback = callback)
        if sync_type == 0: 
            return req.value
        if sync_type == 3:
            req.auto_close = False
        return req

    def close(self):
        """
            Close the connection and the socket. 
        """
        if self.connection_status == "closed": return
        item = {
            'abort' : True,
            'event' : threading.Event()
        }
        self.write_thread_queue.append(item)
        self.write_thread_semaphore.release() # notify new item.
        item['event'].wait(1)
        if not item['event'].isSet():
            _log.warning("write thread doesn't process our abort command")
        try:
            self.handler._shutdown()
        except Exception:
            _log.error("Error when shutting down the handler: %s",
                       traceback.format_exc())
        try:
            self._sck.shutdown(socket.SHUT_RDWR)
        except socket.error:
            pass
        self._sck.close()
        self.connection_status = "closed"
    
    def write_line(self, data):
        """
            Write a line *data* to socket. It appends a **newline** at
            the end of the *data* before sending it.
            
            The string MUST NOT contain **newline** otherwise an AssertionError will
            raise.
            
            Parameters:
            
            **data**
                String containing the data to be sent.
        """
        assert('\n' not in data)
        self.write_lock.acquire()
        try:
            try:
                data = data.encode('utf-8')
            except AttributeError:
                pass
            if self._debug_socket: 
                _log.debug("<:%d: %s", len(data), data.decode('utf-8')[:130])

            self._wbuffer += data + b'\n'
            sbytes = 0
            while self._wbuffer:
                try:
                    sbytes = self._sck.send(self._wbuffer)
                except IOError:
                    _log.debug("Read socket error: IOError (timeout: %r)",
                        self._sck.gettimeout())
                    _log.debug(traceback.format_exc(0))
                    return 0
                except socket.error:
                    _log.debug("Read socket error: socket.error (timeout: %r)",
                        self._sck.gettimeout())
                    _log.debug(traceback.format_exc(0))
                    return 0
                except:
                    raise
                if sbytes == 0: 
                    break
                self._wbuffer = self._wbuffer[sbytes:]
            if self._wbuffer:
                _log.warning("%d bytes left in write buffer", len(self._wbuffer))
            return len(self._wbuffer)
        finally:
            self.write_lock.release()
            


    def read_line(self):
        """
            Read a line of *data* from socket. It removes the `\\n` at
            the end before returning the value.
            
            If the original packet contained `\\n`, the message will be decoded
            as two or more messages.
            
            Returns the line of *data* received from the socket.
        """
        
        self.read_lock.acquire()
        try:
            data = self._readn()
            if len(data) and self._debug_socket: 
                _log.debug(">:%d: %s", len(data), data.decode('utf-8')[:130])
            return data.decode('utf-8')
        finally:
            self.read_lock.release()
            
    def settimeout(self, operation, timeout):
        """
            configures a timeout for the connection for a given operation.
            operation is one of "read" or "write"
        """
        if operation in self._maxtimeout:
            maxtimeout = self._maxtimeout[operation]
        else:
            maxtimeout = None
            
        if maxtimeout is not None:
            if timeout is None or timeout > maxtimeout: 
                timeout = maxtimeout
            
        self._sck.settimeout(timeout)
            
    
    def write_thread(self):
        abort = False
        while not abort:
            self.write_thread_semaphore.acquire() 
            try:
                item = self.write_thread_queue.pop(0)
            except IndexError: # pop from empty list?
                _log.warning("write queue was empty??")
                continue
            abort = item.get("abort", False)
            event = item.get("event")
            write_data  = item.get("write_data")
            if write_data: item["result"] = self.write_now(write_data)
            if event: event.set()
        if self._debug_socket:
            _log.debug("Writing thread finished.")
            
            
    def write(self, data, timeout = None):
        item = {
            'write_data' : data
        }
        self.write_thread_queue.append(item)
        self.write_thread_semaphore.release() # notify new item.

    def write_now(self, data, timeout = None):
        """ 
            Standard function to write to the socket 
            which by default points to write_line
        """
        #self.scklock.acquire()
        self.settimeout("write", timeout)
        ret = None
        #try:
        ret = self.write_line(data)
        #finally:
        #    self.scklock.release()
        return ret
    
    def read(self, timeout = None):
        """ 
            Standard function to read from the socket 
            which by default points to read_line
        """
        ret = None
        self.scklock.acquire()
        self.settimeout("read", timeout)
        try:
            ret = self.read_line()
        finally:
            self.scklock.release()
        return ret

    def _readn(self):
        """
            Internal function which reads from socket waiting for a newline
        """
        streambuffer = self._buffer
        pos = streambuffer.find(b'\n')
        #_log.debug("read...")
        #retry = 0
        while pos == -1:
            data = b''
            try:
                data = self._sck.recv(2048)
            except IOError as inst:
                _log.debug("Read socket error: IOError%r (timeout: %r)",
                    inst.args, self._sck.gettimeout())
                if inst.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    if self._sck.gettimeout() == 0: # if it was too fast
                        self._sck.settimeout(5)
                        continue
                        #time.sleep(0.5)
                        #retry += 1
                        #if retry < 10:
                        #    _log.debug("Retry %s", retry)
                        #    continue
                #_log.debug(traceback.format_exc(0))
                if inst.errno in self._SOCKET_COMM_ERRORS:
                    raise EofError(len(streambuffer))                
                
                return b''
            except socket.error as inst:
                _log.error("Read socket error: socket.error%r (timeout: %r)", 
                    inst.args, self._sck.gettimeout())
                #_log.debug(traceback.format_exc(0))
                return b''
            except:
                raise
            if not data:
                raise EofError(len(streambuffer))
            #_log.debug("readbuf+: %r", data)
            streambuffer += data
            pos = streambuffer.find(b'\n')

        self._buffer = streambuffer[pos + 1:]
        streambuffer = streambuffer[:pos]
        #_log.debug("read: %r", buffer)
        return streambuffer
        
    def serve(self):
        """
            Basic function to put the connection serving. Usually is better to 
            use server.Server class to do this, but this would be useful too if 
            it is run from a separate Thread.
        """
        try:
            while True: 
                self.read_and_dispatch()
        finally:
            self.close()
