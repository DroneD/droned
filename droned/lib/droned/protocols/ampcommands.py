from twisted import protocols

class ProcessStarted(protocols.amp.Command):
    arguments = [('pid', protocols.amp.Integer()),]
    response = []

class ProcessLost(protocols.amp.Command):
    arguments = [('pid', protocols.amp.Integer()),]
    response = []

class ProcessStdout(protocols.amp.Command):
    arguments = [('pid', protocols.amp.Integer()),
                 ('data', protocols.amp.String())]
    response = []

class ProcessStderr(protocols.amp.Command):
    arguments = [('pid', protocols.amp.Integer()),
                 ('data', protocols.amp.String())]
    response = []

class ProcessExited(protocols.amp.Command):
    arguments = [('pid', protocols.amp.Integer()),
                 ('exitCode', protocols.amp.Integer())]
    response = []

class SystemCtrl(protocols.amp.Command):
    arguments = [('service', protocols.amp.String()),
                 ('action', protocols.amp.String()),
                 ('argstr', protocols.amp.String())]
    response = [('code', protocols.amp.Integer()),
                ('description', protocols.amp.String()),
                ('signal', protocols.amp.String()),
                ('status', protocols.amp.Integer())]

class Command(protocols.amp.Command):
    arguments = [
        ('pickledArguments', protocols.amp.String())
    ]
    response = [('code', protocols.amp.Integer()),
                ('description', protocols.amp.String()),
                ('signal', protocols.amp.String()),
                ('status', protocols.amp.Integer())]

class SystemSettings(protocols.amp.Command):
    arguments = [('state', protocols.amp.String()),]
    response = []
