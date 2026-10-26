# gerado por fpga-new (DE0-CV) — recriado a cada adocao
package require ::quartus::project
package require ::quartus::sta

if {$argc != 1} {
  error "uso: quartus_sta -t ../../scripts/check_timing.tcl <projeto>"
}

set project [lindex $argv 0]
project_open $project
create_timing_netlist
read_sdc
update_timing_netlist

set failed 0
foreach_in_collection op [get_available_operating_conditions] {
  set corner [get_operating_conditions_info $op -display_name]
  set_operating_conditions $op
  update_timing_netlist

  puts "TIMING corner: $corner"
  foreach analysis {setup hold recovery removal} {
    set result [report_timing -$analysis -npaths 1]
    set paths [lindex $result 0]
    set slack [lindex $result 1]

    if {$paths == 0} {
      puts [format "TIMING   %-8s sem caminhos aplicaveis" $analysis]
    } else {
      puts [format "TIMING   %-8s pior slack = %s ns" $analysis $slack]
      if {$slack < 0.0} {
        set failed 1
      }
    }
  }
}

delete_timing_netlist
project_close

if {$failed} {
  error "timing nao atendido: existe slack negativo"
}

puts "TIMING PASS: nenhum slack negativo em nenhum canto"
