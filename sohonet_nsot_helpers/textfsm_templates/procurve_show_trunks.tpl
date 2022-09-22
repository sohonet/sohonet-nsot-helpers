Value Port (\S+)
Value Name (\S*)
Value PortType (\S+)
Value Group (Trk\d+)
Value TrunkType (\S+)

Start
  ^\s+${Port}\s+\| (${Name})?\s+${PortType}\s+\| ${Group}\s+${TrunkType} -> Record