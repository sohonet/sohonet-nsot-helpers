Value Vid (\d+)
Value Vlan (\S+)
Value IPConfig (\S+)
Value IPAddress (\d+\.\d+\.\d+\.\d+)
Value SubnetMask (\d+\.\d+\.\d+\.\d+)


Start
  ^\s+($Vid\s+)?${Vlan}\s+(\| )?${IPConfig}\s+${IPAddress}\s+${SubnetMask} -> Record
