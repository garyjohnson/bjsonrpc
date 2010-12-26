"""
    bjson/proxies.py
    
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
# import weakref

class Proxy(object):
    """
    Object that forwards calls to the remote.
    
    Parameters:
    
    **conn**
        Connection object to forward calls.
        
    **sync_type**
        synchronization type. 0-synchronous. 1-asynchronous. 2-notification.
        
    **obj** = None
        optional. Object name to call their functions, (used to proxy functions of *RemoteObject*
        
    """
    def __init__(self, conn, sync_type, obj = None):
        self._conn = conn
        self._obj = obj
        self.sync_type = sync_type

    def __getattr__(self, name):
        if self._obj:
            name = "%s.%s" % (self._obj,name)
            
        def fn(*args, **kwargs):
            return self._conn._proxy(self.sync_type, name, args, kwargs)
        #print name
        fn.__name__ = str(name)
        fn._conn = self._conn
        fn.sync_type = self.sync_type
        
        return fn
        