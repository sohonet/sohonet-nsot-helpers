Value Vlan (\d+)
Value Name (.*?)
Value Status (\S+)
Value Voice (Yes|No)
Value Jumbo (Yes|No)

Start
  ^\s+${Vlan}\s+${Name}\s+(\| )?${Status}\s+(${Voice}\s+)?${Jumbo} -> Record