Value Prefix (\S+)
Value NextHop (\S+)
Value Name (\S+)
Value Vrf (\S+)

Start
  ^ip route(?: vrf ${Vrf})? ${Prefix} ${NextHop}(?: name ${Name})? -> Record
  ^! -> Clearall