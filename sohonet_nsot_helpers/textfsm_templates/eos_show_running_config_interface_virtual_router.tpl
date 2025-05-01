Value Interface (\S+)
Value IPAddress (\S+)

Start
  ^interface ${Interface}
  ^\s+ip virtual-router address ${IPAddress} -> Record
  ^! -> Clearall
