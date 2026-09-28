"""In-memory MAX boundary including deletion of temporary messages."""
class RecordingMax:
    def __init__(self, sent):
        self.sent=sent
        self.messages={}
        self.counter=0

    async def send(self, user, reply):
        self.counter+=1
        mid=str(self.counter)
        self.messages[mid]=reply
        self.sent.append(reply)
        return {'message': {'body': {'mid': mid}}}

    async def request(self, method, path, *, params):
        assert (method,path)==('DELETE','/messages')
        self.sent.remove(self.messages.pop(params['message_id']))
        return {'success': True}

    async def answer_callback(self, *args): pass
