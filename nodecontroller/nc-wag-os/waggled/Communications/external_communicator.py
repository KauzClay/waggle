#!/usr/bin/env python

import socket, os, os.path, time, pika
from multiprocessing import Process, Queue, Value
import sys
sys.path.append('../../../../devtools/protocol_common/')
from protocol.PacketHandler import *
#sys.path.append('..')
from device_dict import DEVICE_DICT

""" 
    The external communicator is the communication channel between the cloud and the DC. It consists of four processes: two pika clients for pushing and pulling to the cloud and two clients for pushing 
    and pulling to the data cache.
"""

#TODO initial queue declaration
#TODO node SSL certificates

#sets the queue name to the hostname of the node for now

with open('/etc/waggle/hostname','r') as file_:
    HOSTNAME = file_.read().strip()

with open('/etc/waggle/queuename','r') as file_:
    QUEUENAME = file_.read().strip()    
    
class external_communicator(object):
    
    def __init__(self):
        pass
    
    incoming = Queue() #stores messages to push into DC 
    outgoing = Queue() #stores messages going out to cloud
    cloud_connected = Value('i') #indicates if the cloud is or is not connected
    
class pika_push(Process):
    """ 
        A pika client for rabbitMQ to push messages to the cloud. When the cloud is connected, the pull client sends pull requests and puts messages into the outgoing queue. 
        When the outgoing queue is not empty, this pika client will connect and push those messages to the cloud.
    """ 
    def run(self):
        comm = external_communicator()
        #creds = pika.PlainCredentials('guest1', 'guest1')
        params = pika.connection.URLParameters("amqps://waggle:waggle@10.10.10.108:5671/%2F") #SSL
        #params = pika.ConnectionParameters('beehive.wa8.gl',5672, '/', creds) 
        #params = pika.ConnectionParameters('10.10.10.108',5672, '/', creds) 
        #params = pika.ConnectionParameters('10.10.10.143',5672, '/', creds) 
        #params = pika.ConnectionParameters('10.10.10.139',5672, '/', creds)
        print 'Pika push started...'
        while True:
            try:
                try: 
                    #connecting to cloud
                    connection = pika.BlockingConnection(params)
                    channel = connection.channel()
                    comm.cloud_connected.value = 1 #set the flag to true when connected to cloud
                    #Creating a queue
                    channel.queue_declare(queue=QUEUENAME)
                    #print 'Pika push connection successful'
                    while comm.outgoing.empty(): #sleeps until there are messages to send
                        time.sleep(1)
                    
                    msg = comm.outgoing.get() # gets the first item in the outgoing queue
                    print 'Pika_push... sending msg to cloud.'
                    channel.basic_publish(exchange='waggle_in', routing_key= 'in', body= msg) #sends to cloud 
                except: 
                    print 'Pika_push currently unable to connect to cloud...' 
                    comm.cloud_connected.value = 0 #set the flag to 0 when not connected to the cloud
                    time.sleep(5)
            except pika.exceptions.ConnectionClosed:
                        print "Pika push connection closed. Waiting and trying again."
                        comm.cloud_connected.value = 0
                        time.sleep(5)
        connection.close()
        
class pika_pull(Process):
    """ 
        A pika client for pulling messages from the cloud. If messages are sent from the cloud, this client will put them into the incoming queue.
    """ 
    
    def run(self):
        print 'Pika pull started...'
        comm = external_communicator()
        #creds = pika.PlainCredentials('guest1', 'guest1')
        params = pika.connection.URLParameters("amqps://waggle:waggle@10.10.10.108:5671/%2F") #SSL
        #params = pika.ConnectionParameters('beehive.wa8.gl',5672, '/', creds) #beehive.wa8.gl
        #params = pika.ConnectionParameters('10.10.10.108',5672, '/', creds) #beehive.wa8.gl
        #params = pika.ConnectionParameters('10.10.10.143',5672, '/', creds)
        #params = pika.ConnectionParameters('10.10.10.139',5672, '/', creds)
        while True: 
            
            try:
                try:
                    connection = pika.BlockingConnection(params) 
                    channel = connection.channel()
                    #print 'Pika pull connection successful.'
                    comm.cloud_connected.value = 1
                     #Creating a queue
                    channel.queue_declare(queue=QUEUENAME)
                    channel.basic_consume(callback, queue=QUEUENAME)
                    #loop that waits for data 
                    channel.start_consuming()
                except:
                    print 'Pika_pull currently unable to connect to cloud. Waiting before trying again.' 
                    comm.cloud_connected.value = 0 #set the flag to 0 when not connected to the cloud
                    time.sleep(5)
               
            except pika.exceptions.ConnectionClosed:
                        print "Pika pull connection closed. Waiting before trying again."
                        comm.cloud_connected.value = 0 #set the flag to false when not connected to the cloud
                        time.sleep(5)
        connection.close()
                
                
#pulls the message from the cloud and puts it into incoming queue 
def callback(ch, method, properties, body):
    comm = external_communicator()
    print 'Callback received message from cloud: ', body
    comm.incoming.put(body) 
    ch.basic_ack(delivery_tag=method.delivery_tag) #RabbitMQ will not delete message until ack received
                
            
        
class external_client_pull(Process):
    """ 
        A client process that connects to the data cache if the cloud is currently connected.  Once a message is recieved, it is put into the outgoing queue. 
        
    """
    
    def run(self):
        print 'External client pull started...'
        comm = external_communicator()
        while True:
            try:
                if comm.cloud_connected.value == 1: #checks if the cloud is connected
                    try:
                        if os.path.exists('/tmp/Data_Cache_server'):
                            client_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                            client_sock.connect('/tmp/Data_Cache_server') #connects to server
                            #print "Client_pull connected to data cache... "
                            data = 'o|' #sends the pull request in the correct format
                            client_sock.send(data)
                            msg = client_sock.recv(4028) #can be changed 
                            if not msg:
                                break
                            else:
                                if msg != 'False':
                                    comm.outgoing.put(msg) #puts the message in the outgoing queue
                                    client_sock.close() #closes socket after each message is sent #TODO is there a better way to do this?
                                else: 
                                    client_sock.close()
                        else:
                            print 'External client pull unable to connect to the data cache... path does not exist!'
                            time.sleep(5)
                    except:
                        print 'External client pull disconnected from data cache. Waiting and trying again.'
                        client_sock.close()
                        time.sleep(5)
                else:
                    print 'External client pull...cloud is not connected. Waiting and trying again.'
                    time.sleep(5)
            except KeyboardInterrupt, k:
                    print "External client pull shutting down."
                    break
        client_sock.close()
        
        
class external_client_push(Process):
    """ 
        A client process that connects to the data cache and pushes incoming messages.
    """
    
    def run(self):
        print 'External client push started...'
        comm = external_communicator()
        
            #TODO clean up if possible
        while True:
            while comm.incoming.empty(): #sleeps until a message arrives in the incoming queue 
                time.sleep(1)
            try: 
                if os.path.exists('/tmp/Data_Cache_server'):
                    client_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    client_sock.connect('/tmp/Data_Cache_server') #opens socket when a message is in the queue
                    #print "Client_push connected to data cache... "
                    msg = comm.incoming.get() #gets the incoming message from the queue
                    client_sock.send(msg) #sends msg
                    client_sock.close()
                else:
                    print "External client push-Unable to connect to Data Cache."
                    time.sleep(5)
            except KeyboardInterrupt, k:
                print "External client push shutting down."
                break
        client_sock.close()
        
        print "Done."
        
       
#uncomment for testing
#if __name__ == "__main__":
    #try:
        ##starts the pull server
        #pika_pull = pika_pull()
        #pika_pull.start()
        
        ##starts the push server 
        #pika_push = pika_push()
        #pika_push.start()
        
        ##starts the push client
        #push_client = external_client_push()
        #push_client.start()
        
        ##starts the pull client
        #pull_client = external_client_pull()
        #pull_client.start()
        #while True:
            #pass
        
    #except KeyboardInterrupt, k:
        #pika_pull.terminate()
        #pika_push.terminate()
        #push_client.terminate()
        #pull_client.terminate()
        #print 'Done.'
        