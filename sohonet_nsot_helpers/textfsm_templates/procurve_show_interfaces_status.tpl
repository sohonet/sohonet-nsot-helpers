Value Port (\S+)
Value Name (\S*)
Value Status (Up|Down)
Value ConfigMode (\S+)
Value Speed (\S+)
Value Type (\S+)
Value TaggedVlans (\S+)
Value UntaggedVlan (\S+)

Start
  ^\s+${Port}\s+(${Name}\s+)?${Status}\s+${ConfigMode}\s+${Speed}\s+${Type}\s+${TaggedVlans}\s+${UntaggedVlan} -> Record
