Value Interface (\S+)
Value InterfaceAcl (\S+)

Start
  ^interface ${Interface}
  ^\s+ip access-group ${InterfaceAcl} in -> Record
  ^! -> Clearall
