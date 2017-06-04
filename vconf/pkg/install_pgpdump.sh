#!/usr/bin/env bash

# cd to import directory
cd /vagrant/vconf || exit 1
source provision_helper.sh

install_package 'python3 -c "import pgpdump"' 'python3-pgpdump' "
pip3 install -U pip;
pip3 install pgpdump
"
