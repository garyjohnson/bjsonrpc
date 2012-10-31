"""
    bjson/jsonlib.py
    
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
try:
    import simplejson as j
except ImportError:
    import json as j
except ImportError:
    print("FATAL: No suitable json library found!")
    raise
from pprint import pprint


def dumps(argobj, conn):
    """
        dumps json object using loaded json library and forwards unknown objects
        to *Connection.dumpObject* function.
    """
    ret = None
    try:
        ret = j.dumps(argobj, separators = (',', ':'), default=conn.dump_object)
    except TypeError:
        pprint(argobj)
        raise
        #raise TypeError("The Python object is not serializable to JSON!")
    return ret

def loads(argobj, conn):
    """
        loads json object using *Connection.load_object* to convert json hinted 
        objects to real objects. 
    """
    ret = None
    try:
        ret = j.loads(argobj, object_hook=conn.load_object)
    except ValueError:
        pprint(argobj)
        raise
        #raise ValueError("The String object is not a valid JSON data!")
    
    return ret
