#!/usr/bin/env bash

# cd to import directory
cd /vagrant/vconf || exit 1
source provision_helper.sh

install_package 'haveged' 'haveged' "
pacman -S --noconfirm haveged
"
