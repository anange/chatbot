#!/usr/bin/python
# coding=utf-8

'''
requires:
    PyYAML
    xmpppy
    (or, use requirements.txt)

description:
    this is a base class. it won't work by itself.
    to make a chatbot, extend this class,
    specifying username, password, chatroom, and
    screen_name as properties of your object.
'''

import datetime
import re
import smtplib
import sys
import xmpp
import yaml


class ChatResponder(list):
    '''
    Used to parse a list of regular expressions
    and match text to which to respond.
    '''
    def __call__(self, *expr, **kwargs):
        ''' response decorator '''
        def decorator(func):
            func.expressions = expr
            func.only_for = kwargs.get('only_respond_to', None)
            func.data = None
            self.append(func)
            return func
        return decorator

    def get_response(self, bot, text, user):
        ''' iterate the list of responses and search for a match '''
        for response in self:
            if response.only_for is None:
                #a response can have multiple regexes
                for exp in response.expressions:
                    m = re.search(exp, text, re.M)
                    if m:
                        #only return if the match gave back text
                        r = response(bot, m, text, user)
                        if r:
                            return r
        return None

    def get_personal_response(self, bot, text, user, target_user):
        ''' search for responses for a specific user '''
        for response in self:
            if response.only_for and response.only_for.lower() == target_user.lower():
                #a response can have multiple regexes
                for exp in response.expressions:
                    m = re.search(exp, text, re.M)
                    if m:
                        #only return if the match gave back text
                        r = response(bot, m, text, user)
                        if r:
                            return r
        return None


responder = ChatResponder()
me_responder = ChatResponder()


class ChatBot():
    #required, override these properties to connect
    username = ''
    password = ''
    chatroom = ''
    screen_name = ''

    #optional, override these properties for more fun
    my_names = []
    ignore_from = []
    aliases = {}
    sign_off = 'brb'

    #internal, don't mess with these
    prev_message = ''
    curr_message = ''
    pile_on = ''
    timeout = None
    silent = False

    def __init__(self, chatroom=None):
        if chatroom:
            self.chatroom = chatroom

        jid = xmpp.protocol.JID(self.username)
        self.jid = jid
        if jid.getDomain() == 'gmail.com':
            self.chat_domain = 'groupchat.google.com'
        else:
            self.chat_domain = 'conference.%s' % jid.getDomain()

        self.full_chatroom = '%s@%s/%s' % (
                self.chatroom, self.chat_domain, self.screen_name)

        self.client = xmpp.Client(jid.getDomain(), debug=[])
        print("connecting to %s..." % self.chat_domain)
        if not self.client.connect():
            print("unable to connect.")
            return
        print("authorizing...")
        if not self.client.auth(jid.getNode(), self.password):
            print("unable to authorize.")
            return
        print('Joining chatroom...')
        self.client.sendInitPresence()
        self.client.RegisterHandler('message', self.message_callback)
        self.client.RegisterHandler('presence', self.presence_callback)
        self.client.send(xmpp.Presence(to=self.full_chatroom))

        self.stop = False
        while not self.stop:
            self.startup = datetime.datetime.now() + datetime.timedelta(minutes=1)

            #load responses when spoken to
            with open('me_responds.yaml') as fh:
                self.me_responds = yaml.load(fh)

            #load generic chat responses
            with open('generic_responds.yaml') as fh:
                self.chat_responds = yaml.load(fh)

            while self.step():
                pass

            self.send_to_chat(self.sign_off)

            #this part doesn't seem to be working.
            #how can i reconnect after an error?
            if not self.stop:
                print('reloading')
                self.startup = datetime.datetime.now() + datetime.timedelta(minutes=1)
                self.client.reconnectAndReauth()

    def step(self):
        try:
            return self.client.Process(1)
        except KeyboardInterrupt:
            self.stop = True
            return False
        #return True

    def presence_callback(self, conn, msg):
        ''' handles presence messages '''
        if msg.getType() == 'groupchat':
            usr = msg.getFrom().getResource()
            print('%s is %s' % (usr, msg.getShow()))
        #else:
        #    print msg

    def message_callback(self, conn, msg):
        ''' process an incoming chat message '''

        #groupchat messages
        if msg.getType() == "groupchat":
            self.handle_groupchat_message(msg)

        #private message
        if msg.getType() == "chat":
            self.handle_private_message(msg)

    def handle_groupchat_message(self, message):
        ''' most of the fun happens here, in groupchat '''

        #handle a 1-minute startup delay, to prevent the last-50 nuissance
        if self.startup:
            if self.startup > datetime.datetime.now():
                return
            else:
                self.startup = None

        #if we have any trouble handling the incoming message, just ignore it
        try:
            msgbody = str(message.getBody().decode('utf-8'))
        except:
            return

        msgfrom = message.getFrom().getResource()
        nicefrom = re.sub(r'\s.*$', '', msgfrom)
        msgtext = msgbody.lower()

        self.curr_message = msgtext

        #ignore some people
        if msgfrom.lower() in self.ignore_from:
            return

        #log what we're seeing. why not, it could help...
        #print str("%s: %s" % (msgfrom, msgtext))

        #first, respond when spoke to...
        #TODO change this to capture youtube links
        if re.search(r'\b(%s)\b' % '|'.join(self.my_names), msgtext):

            #process responses in me_responder
            out = me_responder.get_response(self, msgtext, nicefrom)
            if out:
                return self.send_to_chat(out)

            if self.silent:
                return self.update_message_state()

            #look through the predefined responses...
            for expression, response in self.me_responds:
                if re.search(expression, msgtext):
                    return self.send_to_chat(response.format(nicefrom))

        #if chatbot is being quiet
        if self.silent:
            return self.update_message_state()

        #if chatbot is taking a timeout
        if self.timeout:
            print('timeout expires: %s' % str(self.timeout))
            if self.timeout > datetime.datetime.now():
                return self.update_message_state()
            else:
                self.timeout = None

    def send_to_chat(self, message):
        ''' dump a message to the chatroom '''
        msg = xmpp.Message(
                to='%s@%s' % (self.chatroom, self.chat_domain),
                typ='groupchat',
                body=message)
        self.client.send(msg)
        self.update_message_state()

    def update_message_state(self):
        ''' some housekeeping '''
        if self.curr_message:
            self.prev_message = self.curr_message
            self.curr_message = None

    #this bitch needs an off switch
    @me_responder(r'^(be )?quiet\b')
    def start_silence(self, m, text, user):
        self.silent = True
        return 'sorry. i\'ll put a lid on it.'

    #and an on switch
    @me_responder(r'^okay,?')
    def end_silence(self, m, text, user):
        self.silent = False
        self.timeout = None
        return 'thanks, %s' % user.lower()

    #let's also support a timeout
    @me_responder(r'\b(that\'?s )?enough\b|\btake a break\b|\bhush\b')
    def set_timeout(self, m, text, user):
        self.timeout = datetime.datetime.now() + datetime.timedelta(minutes=10)
        return 'i\'m gonna stay quiet for a bit.'

def main():
    ''' release the kraken... '''
    if len(sys.argv) > 1:
        ChatBot(chatroom=sys.argv[1])
    else:
        ChatBot()

if __name__ == '__main__':
    main()
