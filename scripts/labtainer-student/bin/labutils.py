import filecmp
import glob
import json
import md5
import os
import shutil
import re
import subprocess
import sys
import time
import zipfile
import ParseStartConfig
import ParseLabtainerConfig
import datetime
import getpass
import socket
import fcntl
import struct
import threading
import LabtainerLogging
import shlex
import stat
import traceback
''' logger is defined in whatever script that invokes the labutils '''
global logger
'''
This software was created by United States Government employees at 
The Center for the Information Systems Studies and Research (CISR) 
at the Naval Postgraduate School NPS.  Please note that within the 
United States, copyright protection is not available for any works 
created  by United States Government employees, pursuant to Title 17 
United States Code Section 105.   This software is in the public 
domain and is not subject to copyright. 
'''


# Error code returned by docker inspect
SUCCESS=0
FAILURE=1

# Create a directory path based on input path
# Note: Do not create if the input path already exists as a directory
#       If input path is a file, remove the file then create directory
def createDirectoryPath(input_path):
    # if it exist as a directory, do not delete (only delete if it is a file)
    if os.path.exists(input_path):
        # exists but is not a directory
        if not os.path.isdir(input_path):
            # remove file then create directory
            os.remove(input_path)
            os.makedirs(input_path)
        #else:
        #    logger.DEBUG("input_path directory (%s) exists" % input_path)
    else:
        # does not exists, create directory
        os.makedirs(input_path)

def is_valid_lab(lab_path):
    # Lab path must exist and must be a directory
    if os.path.exists(lab_path) and os.path.isdir(lab_path):
        # Assume it is valid lab then
        logger.DEBUG("lab_path directory (%s) exists" % lab_path)
    else:
        logger.ERROR("Invalid lab! lab_path directory (%s) does not exist!" % lab_path)
        traceback.print_exc()
        traceback.print_stack()
        sys.exit(1)

def get_ip_address(ifname):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return socket.inet_ntoa(fcntl.ioctl(
        s.fileno(),
        0x8915,  # SIOCGIFADDR
        struct.pack('256s', ifname[:15])
    )[20:24])


def isalphadashscore(name):
    # check name - alphanumeric,dash,underscore
    return re.match(r'^[a-zA-Z0-9_-]*$', name)

# get docker0 IP address
def getDocker0IPAddr():
    return get_ip_address('docker0')

# Parameterize my_container_name container
def ParameterizeMyContainer(mycontainer_name, container_user, container_password, lab_instance_seed, user_email, labname):
    retval = True
    cmd_path = '/home/%s/.local/bin/parameterize.sh' % (container_user)
    if container_password == "":
        container_password = container_user
    command=['docker', 'exec', '-i',  mycontainer_name, cmd_path, container_user, container_password, lab_instance_seed, user_email, labname, mycontainer_name ]
    logger.DEBUG("About to call parameterize.sh with : %s" % str(command))
    #return retval 
    child = subprocess.Popen(command, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    error_string = child.stderr.read().strip()
    if len(error_string) > 0:
        if not error_string.startswith('[sudo]'):
            logger.ERROR('ParameterizeMyContainer %s' % error_string)
            retval = False
    out_string = child.stderr.read().strip()
    if len(out_string) > 0:
        logger.DEBUG('ParameterizeMyContainer %s' % out_string)
    return retval

# Start my_container_name container
def StartMyContainer(mycontainer_name):
    retval = True
    if IsContainerRunning(mycontainer_name):
        logger.ERROR("Container %s is already running!\n" % (mycontainer_name))
        sys.exit(1)
    command = "docker start %s  > /dev/null" % mycontainer_name
    logger.DEBUG("Command to execute is (%s)" % command)
    ps = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    output = ps.communicate()
    if len(output[1]) > 0:
        logger.ERROR('StartMyContainer %s' % output[1])
        logger.ERROR('command was %s' % command)
        retval = False
    return retval

# Check to see if my_container_name container has been created or not
def IsContainerCreated(mycontainer_name):
    retval = True
    command = "docker inspect -f {{.Created}} --type container %s 2> /dev/null" % mycontainer_name
    logger.DEBUG("Command to execute is (%s)" % command)
    result = subprocess.call(command, shell=True, stderr=subprocess.PIPE)
    if result == FAILURE:
       retval = False
    logger.DEBUG("Result of subprocess.call IsContainerCreated is %s" % result)
    return retval

def ConnectNetworkToContainer(mycontainer_name, mysubnet_name, mysubnet_ip):
    logger.DEBUG("Connecting more network subnet to container %s" % mycontainer_name)
    command = "docker network connect --ip=%s %s %s 2> /dev/null" % (mysubnet_ip, mysubnet_name, mycontainer_name)
    logger.DEBUG("Command to execute is (%s)" % command)
    result = subprocess.call(command, shell=True)
    logger.DEBUG("Result of subprocess.call ConnectNetworkToContainer is %s" % result)
    return result

def DisconnectNetworkFromContainer(mycontainer_name, mysubnet_name):
    logger.DEBUG("Disconnecting more network subnet to container %s" % mycontainer_name)
    command = "docker network disconnect %s %s 2> /dev/null" % (mysubnet_name, mycontainer_name)
    logger.DEBUG("Command to execute is (%s)" % command)
    result = subprocess.call(command, shell=True)
    logger.DEBUG("Result of subprocess.call DisconnectNetworkFromContainer is %s" % result)
    return result

def CreateSingleContainer(container, mysubnet_name=None, mysubnet_ip=None):
    logger.DEBUG("Create Single Container")
    retval = True
    cmd = "docker inspect -f '{{.Created}}' --type image %s" % container.image_name
    ps = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    output = ps.communicate()
    #s = " --dns "
    #dns = s.join(GetDNS())
    if len(output[1]) > 0:
        logger.DEBUG("Command was (%s)" % cmd)
        logger.ERROR("CreateSingleContainer image %s does not exist!" % container.image_name)
        retval = False
    else:

        docker0_IPAddr = getDocker0IPAddr()
        logger.DEBUG("getDockerIPAddr result (%s)" % docker0_IPAddr)
        volume=''
        if container.script == 'NONE':
            ''' a systemd container, centos or ubuntu? '''
            if IsUbuntuSystemd(container.image_name):
                volume='--security-opt seccomp=confined --tmpfs /run --tmpfs /run/lock -v /sys/fs/cgroup:/sys/fs/cgroup:ro'
            else:
                volume='-v /sys/fs/cgroup:/sys/fs/cgroup:ro'
        elif container.x11.lower() == 'yes':
            #volume = '-e DISPLAY -v /tmp/.Xll-unix:/tmp/.X11-unix --net=host -v$HOME/.Xauthority:/home/developer/.Xauthority'
            volume = '--env="DISPLAY"  --volume="/tmp/.X11-unix:/tmp/.X11-unix:rw"'
        add_hosts = ''     
        for item in container.add_hosts:
            ip, host = item.split(':')
            add_this = '--add-host %s ' % item
            add_hosts += add_this
        if mysubnet_name:
            createsinglecommand = "docker create -t --network=%s --ip=%s --privileged --add-host my_host:%s %s --name=%s --hostname %s %s %s %s" % (mysubnet_name, mysubnet_ip, docker0_IPAddr, add_hosts,  container.full_name, container.hostname, volume, container.image_name, container.script)
        else:
            createsinglecommand = "docker create -t --privileged --add-host my_host:%s %s --name=%s --hostname %s %s %s %s " % (docker0_IPAddr, add_hosts, 
               container.full_name, container.hostname, volume, container.image_name, container.script)
        logger.DEBUG("Command to execute was (%s)" % createsinglecommand)

        ps = subprocess.Popen(createsinglecommand, shell=True, stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        output = ps.communicate()
        if len(output[1]) > 0:
            logger.DEBUG('command was %s' % createsinglecommand)
            logger.ERROR('CreateSingleContainer %s' % output[1])
            retval = False
    return retval


# Create SUBNETS
def CreateSubnets(subnets):
    #for (subnet_name, subnet_network_mask) in networklist.iteritems():
    for subnet_name in subnets:
        subnet_network_mask = subnets[subnet_name].mask
        logger.DEBUG("subnet_name is %s" % subnet_name)
        logger.DEBUG("subnet_network_mask is %s" % subnet_network_mask)

        command = "docker network inspect %s 2> /dev/null" % subnet_name
        logger.DEBUG("Command to execute is (%s)" % command)
        inspect_result = subprocess.call(command, shell=True)
        logger.DEBUG("Result of subprocess.call CreateSubnets docker network inspect is %s" % inspect_result)
        if inspect_result == FAILURE:
            # Fail means does not exist - then we can create
            if subnets[subnet_name].gateway != None:
                logger.DEBUG(subnets[subnet_name].gateway)
                subnet_gateway = subnets[subnet_name].gateway
                command = "docker network create -d bridge --gateway=%s --subnet %s %s" % (subnet_gateway, subnet_network_mask, subnet_name)
            else:
                command = "docker network create -d bridge --subnet %s %s" % (subnet_network_mask, subnet_name)
            logger.DEBUG("Command to execute is (%s)" % command)
            #create_result = subprocess.call(command, shell=True)
            ps = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            output = ps.communicate()
            logger.DEBUG("Result of subprocess.call CreateSubnets docker network create is %s" % output[0])
            if len(output[1]) > 0:
                logger.ERROR("Failed to create %s subnet at %s, %s\n" % (subnet_name, subnet_network_mask, output[1]))
                logger.ERROR("command was %s\n" % command)
                sys.exit(1)
        else:
            logger.WARNING("Already exists! Not creating %s subnet at %s!\n" % (subnet_name, subnet_network_mask))

def RemoveSubnets(subnets, ignore_stop_error):
    for subnet_name in subnets:
        command = "docker network rm %s 2> /dev/null" % subnet_name
        ps = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        output = ps.communicate()
        if len(output[1]) > 0:
            if ignore_stop_error:
                logger.DEBUG('Encountered error removing subnet %s' % subnet_name)
            else:
                logger.ERROR('Encountered error removing subnet %s' % subnet_name)

EMAIL_TMP='./.tmp/email.txt' 
def getLastEmail():
    retval = None
    if os.path.isfile(EMAIL_TMP):
        with open(EMAIL_TMP) as fh:
            retval = fh.read()
    return retval

def putLastEmail(email):
    try:
        os.mkdir('./.tmp')
    except:
        pass
    with open(EMAIL_TMP, 'w') as fh:
            fh.write(email)

def ParamForStudent(lab_master_seed, mycontainer_name, container_user, container_password, labname, student_email, quiet_start):
    if student_email is not None:
        user_email = student_email
    else:
        done = False
        while not done and student_email is None:
            done = True
            # Prompt user for e-mail address
            eprompt = 'Please enter your e-mail address: '
            prev_email = getLastEmail()
            if prev_email is not None:
                eprompt = eprompt+" [%s]" % prev_email

	    #checks if quiet_start is true
            if quiet_start and prev_email is not None:
                user_email = prev_email
            else:
                user_email = raw_input(eprompt)

            #user_email = raw_input(eprompt)
            if len(user_email.strip()) == 0:
                if prev_email is None:
                    done = False
                else:
                    user_email = prev_email
            else:
                putLastEmail(user_email)
    
    # Create hash using LAB_MASTER_SEED concatenated with user's e-mail
    # LAB_MASTER_SEED is per laboratory - specified in start.config
    string_to_be_hashed = '%s:%s' % (lab_master_seed, user_email)
    mymd5 = md5.new()
    mymd5.update(string_to_be_hashed)
    mymd5_hex_string = mymd5.hexdigest()
    logger.DEBUG(mymd5_hex_string)

    if not ParameterizeMyContainer(mycontainer_name, container_user, container_password, mymd5_hex_string,
                                                          user_email, labname):
        logger.ERROR("Failed to parameterize lab container %s!\n" % mycontainer_name)
        sys.exit(1)
    return user_email

# Do InstDocsToHostDir - extract students' docs.zip if exist
def InstDocsToHostDir(start_config, labtainer_config, lab_path, role, is_regress_test, quiet_start):
    labname = start_config.labname
    xfer_dir = os.path.join(labtainer_config.host_home_xfer, labname)
    username = getpass.getuser()
    host_home_xfer = '/home/%s/%s' % (username, xfer_dir)
    logger.DEBUG("path to work with is (%s)" % host_home_xfer)
    logger.DEBUG("labname is (%s)" % labname)
    docsdir_created = False
    docsdir_path = '%s/docs' % host_home_xfer

    # create temporary directory
    tmpdir = '%s/.tmpdir' % host_home_xfer
    createDirectoryPath(tmpdir)

    split_string = '.%s.zip' % labname

    zip_filelist = glob.glob('%s/*.zip' % host_home_xfer)
    logger.DEBUG("filenames is (%s)" % zip_filelist)
    tmpdocszip = '%s/docs.zip' % tmpdir
    # Process each zip file in host_home_xfer
    for fname in zip_filelist:
        ZipFileName = os.path.basename(fname)
        # Note: at this point the ZipFileName should not have the 'containername' yet
        #       the format should be <student_email>.<labname>.zip
        logger.DEBUG("ZipFileName is (%s)" % ZipFileName)

        # Try unpacking the zip file into temporary directory to check if docs.zip exist
        zipoutput = zipfile.ZipFile(fname, "r")
        ''' retain dates of student files '''
        for zi in zipoutput.infolist():
            zipoutput.extract(zi, tmpdir)
            date_time = time.mktime(zi.date_time + (0, 0, -1))
            dest = os.path.join(tmpdir, zi.filename)
            os.utime(dest, (date_time, date_time))
        zipoutput.close()

        # If docs.zip exist
        if os.path.exists(tmpdocszip):
            # Time to create docs directory if it hasn't been created
            if not docsdir_created:
                docsdir_created = True
                createDirectoryPath(docsdir_path)

            # Note: at this point the ZipFileName should not have the 'containername' yet
            #       the format should be <student_email>.<labname>.zip
            splitlist = ZipFileName.split(split_string)
            student_email = splitlist[0]
            student_emaildir = '%s/%s' % (docsdir_path, student_email)
            logger.DEBUG("student_email is (%s)" % student_email)
            logger.DEBUG("student_emaildir is (%s)" % student_emaildir)

            # Create student's e-mail directory (if it does not exist)
            createDirectoryPath(student_emaildir)
            # Unpacking the docs.zip file into student's e-mail directory
            zipoutput = zipfile.ZipFile(tmpdocszip, "r")
            ''' retain dates of student files '''
            for zi in zipoutput.infolist():
                zipoutput.extract(zi, student_emaildir)
                date_time = time.mktime(zi.date_time + (0, 0, -1))
                dest = os.path.join(student_emaildir, zi.filename)
                os.utime(dest, (date_time, date_time))
            zipoutput.close()

        # remove and re-create temporary directory for the students' zip file
        shutil.rmtree(tmpdir, ignore_errors=True)
        os.makedirs(tmpdir)

    # Finally done for all students' zip file in the host_home_xfer directory
    # Final removal of temporary directory
    shutil.rmtree(tmpdir, ignore_errors=True)


# Copy Students' Artifacts from host to instructor's lab container
def CopyStudentArtifacts(labtainer_config, mycontainer_name, labname, container_user, container_password, is_regress_test):
    # Set the lab name 
    command = 'docker exec %s script -q -c "echo %s > /home/%s/.local/.labname" /dev/null' % (mycontainer_name, labname, container_user)
    logger.DEBUG("Command to execute is (%s)" % command)
    result = subprocess.call(command, shell=True)
    logger.DEBUG("Result of subprocess.call CopyStudentArtifacts set labname is %s" % result)
    if result == FAILURE:
        logger.ERROR("Failed to set labname in container %s!\n" % mycontainer_name)
        sys.exit(1)

    # Create is_grade_container
    command = 'docker exec %s script -q -c "echo TRUE > /home/%s/.local/.is_grade_container" /dev/null' % (mycontainer_name, container_user)
    logger.DEBUG("Command to execute is (%s)" % command)
    result = subprocess.call(command, shell=True)
    logger.DEBUG("Result of subprocess.call CopyStudentArtifacts create is_grade_container is %s" % result)
    if result == FAILURE:
        logger.ERROR("Failed to create is_grade_container in container %s!\n" % mycontainer_name)
        sys.exit(1)

    username = getpass.getuser()
    if not is_regress_test == None:
        xfer_dir = os.path.join(labtainer_config.testsets_root, labname)
	xfer_dir += "/" + is_regress_test
        zip_filelist = glob.glob('%s/*.zip' % xfer_dir)
    else:
        xfer_dir = os.path.join(labtainer_config.host_home_xfer, labname)
        zip_filelist = glob.glob('/home/%s/%s/*.zip' % (username, xfer_dir))
    logger.DEBUG("filenames is (%s)" % zip_filelist)
    # Copy zip files from 'Shared' folder to 'home/$CONTAINER_USER'
    for fname in zip_filelist:
        logger.DEBUG("name is %s" % fname)
        base_fname = os.path.basename(fname)
        # Copy zip file and chown it
        command = 'docker cp %s %s:/home/%s/' % (fname, mycontainer_name, container_user)
        logger.DEBUG("Command to execute is (%s)" % command)
        result = subprocess.call(command, shell=True)
        logger.DEBUG("Result of subprocess.call CopyStudentArtifacts copy zipfile (%s) is %s" % (fname, result))
        if result == FAILURE:
            logger.ERROR("Failed to set labname in container %s!\n" % mycontainer_name)
            sys.exit(1)
        #command = 'docker exec %s echo "%s\n" | sudo -S chown %s:%s /home/%s/%s' % (mycontainer_name, container_password, 
        #             container_user, container_user, container_user, base_fname)
        command = 'docker exec %s chown %s:%s /home/%s/%s' % (mycontainer_name, 
                     container_user, container_user, container_user, base_fname)
        logger.DEBUG("Command to execute is (%s)" % command)
        result = subprocess.call(command, shell=True)
        logger.DEBUG("Result of subprocess.call CopyStudentArtifacts copy zipfile (%s) is %s" % (fname, result))
        if result == FAILURE:
            logger.ERROR("Failed to set labname in container %s!\n" % mycontainer_name)
            sys.exit(1)

def GetRunningContainersList():
    retval = True
    cmd = "docker container ls --format {{.Names}}"
    ps = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    output = ps.communicate()
    if len(output[1].strip()) > 0:
        logger.DEBUG('No running containers: error returned %s, return false' % output[1])
        return False, None
    result = output[0].strip()
    logger.DEBUG('result is %s' % result)
    if 'Error:' in result or len(result.strip()) == 0:
        if 'Error:' in result:
            logger.DEBUG("Command was (%s)" % cmd)
            logger.DEBUG("Error from command = '%s'" % result)
        return False, result
    containers_list = result.split('\n')
    return True, containers_list

def GetRunningLabNames(containers_list, role):
    labnameslist = []
    found_lab_role = False
    for each_container in containers_list:
        if each_container.endswith(role):
            splitstring = each_container.split('.')
            labname = splitstring[0]
            found_lab_role = True
            if labname not in labnameslist:
                labnameslist.append(labname)
    return found_lab_role, labnameslist

def ImageExists(image_name, container_name):
    retval = True
    logger.DEBUG('check existence of container %s image %s' % (container_name, image_name))
    cmd = "docker inspect -f '{{.Created}}' --type image %s" % image_name
    ps = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    output = ps.communicate()
    if len(output[1].strip()) > 0:
        logger.DEBUG('No image: error returned %s, return false' % output[1])
        return False, None
    result = output[0].strip()
    logger.DEBUG('result is %s' % result)
    if 'Error:' in result or len(result.strip()) == 0:
        if 'Error:' in result:
            logger.DEBUG("Command was (%s)" % cmd)
            logger.DEBUG("Error from command = '%s'" % result)
        return False, result
    return True, result

def RebuildLab(lab_path, role, is_regress_test=None, force_build=False, quiet_start=False):
    # Pass 'True' to ignore_stop_error (i.e., ignore certain error encountered during StopLab
    #                                         since it might not even be an error)
    labname = os.path.basename(lab_path)
    StopLab(lab_path, role, True)
    logger.DEBUG('Back from StopLab')
    DoRebuildLab(lab_path, role, is_regress_test, force_build)

    # Check existence of /home/$USER/$HOST_HOME_XFER directory - create if necessary
    config_path       = os.path.join(lab_path,"config") 
    start_config_path = os.path.join(config_path,"start.config")
   
    start_config = ParseStartConfig.ParseStartConfig(start_config_path, labname, role, logger)
    labtainer_config_dir = os.path.join(os.path.dirname(os.path.dirname(lab_path)), 'config', 'labtainer.config')
    labtainer_config = ParseLabtainerConfig.ParseLabtainerConfig(labtainer_config_dir, logger)
    host_home_xfer = labtainer_config.host_home_xfer
    myhomedir = os.environ['HOME']
    host_xfer_dir = '%s/%s' % (myhomedir, host_home_xfer)
    CreateHostHomeXfer(host_xfer_dir)
    DoStart(start_config, labtainer_config, lab_path, role, is_regress_test, quiet_start)

def DoRebuildLab(lab_path, role, is_regress_test=None, force_build=False):
    labname = os.path.basename(lab_path)
    is_valid_lab(lab_path)
    config_path       = os.path.join(lab_path,"config") 
    start_config_path = os.path.join(config_path,"start.config")
   
    start_config = ParseStartConfig.ParseStartConfig(start_config_path, labname, role, logger)
    labtainer_config_dir = os.path.join(os.path.dirname(os.path.dirname(lab_path)), 'config', 'labtainer.config')
    labtainer_config = ParseLabtainerConfig.ParseLabtainerConfig(labtainer_config_dir, logger)
    host_home_xfer = labtainer_config.host_home_xfer

    build_student = 'bin/buildImage.sh'
    build_instructor = 'bin/buildInstructorImage.sh'
    LABS_DIR = os.path.abspath('../../labs')
    didfix = False
    ''' hackey assumption about running from labtainers-student or labtainers-instructor '''
    if role == 'instructor':
        container_bin = './assess_bin'
    else:
        container_bin = './lab_bin'
    for name, container in start_config.containers.items():
        mycontainer_name       = container.full_name
        mycontainer_image_name = container.image_name
        cmd = 'docker rm %s' % mycontainer_name
        ps = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        output = ps.communicate()
        logger.DEBUG("Command was (%s)" % cmd)
        if len(output[1]) > 0:
            logger.DEBUG("Error from command = '%s'" % str(output[1]))
        force_this_build = force_build
        if not force_this_build:
            image_exists, result = ImageExists(mycontainer_image_name, mycontainer_name)
            if not image_exists:
                force_this_build = True
        else:
            image_exists = True
        if force_this_build or CheckBuild(lab_path, mycontainer_image_name, mycontainer_name, name, role, True, container_bin, start_config.grade_container):
            print('Will call buildImage to build %s' % mycontainer_name)
            logger.DEBUG("Will rebuild %s, Image exists(ignore if force): %s force_this_build: %s" % (mycontainer_name, 
                image_exists, force_this_build))
            if os.path.isfile(build_student):
                cmd = '%s %s %s %s %s %s %s %s' % (build_student, labname, name, container.user, container.password, True, LABS_DIR, labtainer_config.apt_source)
            elif os.path.isfile(build_instructor):
                cmd = '%s %s %s %s %s %s %s %s' % (build_instructor, labname, name, container.user, container.password, True, LABS_DIR, labtainer_config.apt_source)
            else:
                logger.ERROR("no image rebuild script\n")
                exit(1)
            logger.DEBUG('cmd is %s' % cmd)     
            ps = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            while True:
                line = ps.stdout.readline()
                if line != '':
                    logger.DEBUG(line)
                else:
                    break
            while True:
                line = ps.stderr.readline()
                if line != '':
                    logger.DEBUG(line)
                else:
                    break
            #if os.system(cmd) != 0:
            #    logger.ERROR("build of image failed\n")
            #    logger.DEBUG('cmd was %s' % cmd)
            #    exit(1)
    return start_config.registry


def DoStart(start_config, labtainer_config, lab_path, role, is_regress_test, quiet_start):
    labname = os.path.basename(lab_path)
    lab_master_seed = start_config.lab_master_seed
    logger.DEBUG("DoStart Multiple Containers and/or multi-home networking")

    # Create SUBNETS
    CreateSubnets(start_config.subnets)
    student_email = None
    for name, container in start_config.containers.items():
        mycontainer_name       = container.full_name
        mycontainer_image_name = container.image_name
        container_user         = container.user
        container_password         = container.password
        container_hostname         = container.hostname

        if is_regress_test and mycontainer_name != start_config.grade_container:
            continue

        haveContainer = IsContainerCreated(mycontainer_name)
        logger.DEBUG("DoStart IsContainerCreated result (%s)" % haveContainer)

        # Set need_seeds=False first
        need_seeds=False

        # IsContainerCreated return False if container does not exists
        if not haveContainer:
            # Container does not exist, create the container
            # Use CreateSingleContainer()
            containerCreated = False
            if len(container.container_nets) == 0:
                containerCreated = CreateSingleContainer(container)
            else:
                mysubnet_name, mysubnet_ip = container.container_nets.popitem()
                containerCreated = CreateSingleContainer(container, mysubnet_name, mysubnet_ip)
                
            logger.DEBUG("CreateSingleContainer result (%s)" % containerCreated)
            if not containerCreated:
                logger.ERROR("CreateSingleContaier fails to create container %s!\n" % mycontainer_name)
                sys.exit(1)

            # Give the container some time -- just in case
            time.sleep(3)
            # If we just create it, then set need_seeds=True
            need_seeds=True

        # Check again - 
        haveContainer = IsContainerCreated(mycontainer_name)
        logger.DEBUG("IsContainerCreated result (%s)" % haveContainer)

        # IsContainerCreated returned False if container does not exists
        if not haveContainer:
            logger.ERROR("Container %s still not created!\n" % mycontainer_name)
            sys.exit(1)
        else:
            for mysubnet_name, mysubnet_ip in container.container_nets.items():
                connectNetworkResult = ConnectNetworkToContainer(mycontainer_name, mysubnet_name, mysubnet_ip)

            # Start the container
            if not StartMyContainer(mycontainer_name):
                logger.ERROR("Container %s failed to start!\n" % mycontainer_name)
                sys.exit(1)

        if role == 'instructor':
            # Do InstDocsToHostDir - extract students' docs.zip if exist
            InstDocsToHostDir(start_config, labtainer_config, lab_path, role, is_regress_test, quiet_start)
            '''
            Copy students' artifacts only to the container where 'Instructor.py' is
            to be run - where <labname>.grades.txt will later reside also (i.e., don't copy to all containers)
            Copy to container named start_config.grade_container
            '''
            if mycontainer_name == start_config.grade_container:
                logger.DEBUG('do CopyStudentArtifacts for %s, labname: %s regress: %s' % (mycontainer_name, labname, is_regress_test))
                copy_result = CopyStudentArtifacts(labtainer_config, mycontainer_name, labname, container_user, container_password, is_regress_test)
                if copy_result == FAILURE:
                    logger.ERROR("Failed to copy students' artifacts to container %s!\n" % mycontainer_name)
                    sys.exit(1)

    	# If the container is just created, then use the previous user's e-mail
        # then parameterize the container
    	elif quiet_start and need_seeds and role == 'student':
            student_email = ParamForStudent(lab_master_seed, mycontainer_name, container_user, container_password, labname, student_email, quiet_start)
        
        elif need_seeds and role == 'student':
            student_email = ParamForStudent(lab_master_seed, mycontainer_name, container_user, container_password, labname, student_email, quiet_start)
    #
    #  If a read_first.txt file exists in the lab's config directory, less it before the student continues.
    #
    doc_dir = os.path.join(lab_path, 'docs')
    read_first = os.path.join(doc_dir, 'read_first.txt')
    pdf = '%s.pdf' % labname
    manual = os.path.join(doc_dir, pdf)

    if os.path.isfile(read_first) and role != 'instructor':
        print '\n\n'
        command = 'less %s' % read_first
        less = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
        sed_cmd = "sed -e s+LAB_MANUAL+%s+ -e s+LAB_DOCS+%s+" %  (manual, doc_dir)
        sed = subprocess.Popen(sed_cmd.split(), stdin=less.stdout)
        less.stdout.close()
        output = sed.communicate()[0]
        less.wait()
        dumb = raw_input("Press <enter> to start lab")

    
    # Reach here - Everything is OK - spawn terminal for each container based on num_terminal
    terminal_count = 0
    for container in start_config.containers.values():
        # Do not spawn terminal if it is regression testing
        if is_regress_test:
            continue
        num_terminal = container.terminals
        mycontainer_name = container.full_name
        logger.DEBUG("Number of terminal is %d" % num_terminal)
        # If this is instructor - spawn 2 terminal for 'grader' container otherwise 1 terminal
        if role == 'instructor':
            if mycontainer_name == start_config.grade_container:
                # hack use startup.sh instead of instructor.py because some profiles already run startup...
                cmd =  'sh -c "cd /home/%s && .local/bin/startup.sh"' % (container.user)
                terminal_location = terminalWideCounter(terminal_count)
                terminal_count += 1
                # note hack to change --geometry to -geometry
                spawn_command = "xterm %s -title GOAL_RESULTS -fa 'Monospace' -fs 11 -e docker exec -it %s %s  &" % (terminal_location[1:], 
                     mycontainer_name, cmd)
                #print spawn_command
                logger.DEBUG("instructor spawn: %s" % spawn_command)
                os.system(spawn_command)
            num_terminal = 1
        else:
            CopyFilesToHost(lab_path, container.name, mycontainer_name, container_user)
        if container.xterm is not None:
                parts = container.xterm.split()
                title = parts[0]
                command = None
                if title.lower() == 'instructions' and len(parts) == 1:
                    if role != 'instructor':
                        command = 'startup.sh'
                elif len(parts) == 2:
                    command = parts[1]
                else:
                    logger.ERROR("Bad XTERM entryin in start.config: %s" % container.xterm)
                    exit(1)
                if command is not None:
                    cmd =  'sh -c "cd /home/%s && .local/bin/%s"' % (container.user, command)
                    terminal_location = terminalCounter(terminal_count)
                    terminal_count += 1
                    # note hack to change --geometry to -geometry
                    spawn_command = "xterm %s -title %s -sb -rightbar -fa 'Monospace' -fs 11 -e docker exec -it %s %s  & 2>/tmp/xterm.out" % (terminal_location[1:], 
                         title, mycontainer_name, cmd)
                    logger.DEBUG("xterm spawn: %s" % spawn_command)
                    #print spawn_command
                    os.system(spawn_command)
                    # race condition, gnome may beat xterm to the startup.sh script
                    time.sleep(1)
        # If the number of terminal is zero -- do not spawn
        if num_terminal != 0:
            for x in range(num_terminal):
                #sys.stderr.write("%d \n" % terminal_count)
                terminal_location = terminalCounter(terminal_count)
                #sys.stderr.write("%s \n" % terminal_location)
                #sys.stderr.write("%s \n" % mycontainer_name)
                terminal_count += 1
                if role == 'instructor':
                    # hack, instructor does not have augmented profile
                    cmd = 'sh -c "cd /home/%s && bash -l"' % container.user
                else:
                    cmd = 'bash -l' 
                #spawn_command = "gnome-terminal %s -x docker exec -it %s bash -l &" % (terminal_location, mycontainer_name)
                spawn_command = 'gnome-terminal %s -- docker exec -it %s %s &' % (terminal_location, 
                   mycontainer_name, cmd)
                logger.DEBUG("gnome spawn: %s" % spawn_command)
                #print spawn_command
                os.system(spawn_command)
                

    return 0

def terminalCounter(terminal_count):
    x_coordinate = 100 + ( 50 * terminal_count )
    y_coordinate = 75 + ( 50 * terminal_count)
    terminal_location = "--geometry 75x25+%d+%d" % (x_coordinate, y_coordinate)
    return terminal_location

def terminalWideCounter(terminal_count):
    x_coordinate = 100 + ( 50 * terminal_count )
    y_coordinate = 75 + ( 50 * terminal_count)
    terminal_location = "--geometry 180x25+%d+%d" % (x_coordinate, y_coordinate)
    return terminal_location

# Check existence of /home/$USER/$HOST_HOME_XFER directory - create if necessary
def CreateHostHomeXfer(host_xfer_dir):
    # remove trailing '/'
    host_xfer_dir = host_xfer_dir.rstrip('/')
    logger.DEBUG("host_home_xfer directory (%s)" % host_xfer_dir)
    if os.path.exists(host_xfer_dir):
        # exists but is not a directory
        if not os.path.isdir(host_xfer_dir):
            # remove file then create directory
            os.remove(host_xfer_dir)
            os.makedirs(host_xfer_dir)
        #else:
        #    logger.DEBUG("host_home_xfer directory (%s) exists" % host_xfer_dir)
    else:
        # does not exists, create directory
        os.makedirs(host_xfer_dir)

# CopyChownGradesFile
def CopyChownGradesFile(mycwd, start_config, labtainer_config, container_name, container_image, container_user, ignore_stop_error):
    host_home_xfer = os.path.join(labtainer_config.host_home_xfer, start_config.labname)
    lab_master_seed = start_config.lab_master_seed
    labname = start_config.labname

    username = getpass.getuser()

    # Copy <labname>.grades.txt file
    grade_filename = '/home/%s/%s.grades.txt' % (container_user, labname)
    command = "docker cp %s:%s /home/%s/%s" % (container_name, grade_filename, username, host_home_xfer)
    logger.DEBUG("Command to execute is (%s)" % command)
    result = subprocess.call(command, shell=True)
    logger.DEBUG("Result of subprocess.Popen exec cp %s.grades.txt file is %s" % (labname, result))
    if result == FAILURE:
        # try grabbing instructor.log
        command = "docker cp %s:/tmp/instructor.log /tmp/instructor.log" % (container_name)
        result = subprocess.call(command, shell=True)
        logger.DEBUG("Result of subprocess.Popen exec cp instructor.log file is %s" % (result))


        StopMyContainer(mycwd, start_config, container_name, ignore_stop_error)
        if ignore_stop_error:
            logger.DEBUG("Container %s fail on executing cp %s.grades.txt file!\n" % (container_name, labname))
        else:
            logger.WARNING("Container %s fail on executing cp %s.grades.txt file!\n" % (container_name, labname))
        return

    '''
    # Change <labname>.grades.txt ownership to defined user $USER
    command = "sudo chown %s:%s /home/%s/%s/%s.grades.txt" % (username, username, username, host_home_xfer, labname)
    logger.DEBUG("Command to execute is (%s)" % command)
    result = subprocess.call(command, shell=True)
    logger.DEBUG("Result of subprocess.Popen exec chown %s.grades.txt file is %s" % (labname, result))
    if result == FAILURE:
        StopMyContainer(mycwd, start_config, container_name, ignore_stop_error)
        if ignore_stop_error:
            logger.DEBUG("Container %s fail on executing chown %s.grades.txt file!\n" % (container_name, labname))
        else:
            logger.ERROR("Container %s fail on executing chown %s.grades.txt file!\n" % (container_name, labname))
        sys.exit(1)
    '''

    # Copy <labname>.grades.json file
    gradejson_filename = '/home/%s/%s.grades.json' % (container_user, labname)
    command = "docker cp %s:%s /home/%s/%s" % (container_name, gradejson_filename, username, host_home_xfer)
    logger.DEBUG("Command to execute is (%s)" % command)
    result = subprocess.call(command, shell=True)
    logger.DEBUG("Result of subprocess.Popen exec cp %s.grades.json file is %s" % (labname, result))
    if result == FAILURE:
        StopMyContainer(mycwd, start_config, container_name, ignore_stop_error)
        if ignore_stop_error:
            logger.DEBUG("Container %s fail on executing cp %s.grades.json file!\n" % (container_name, labname))
        else:
            logger.WARNING("Container %s fail on executing cp %s.grades.json file!\n" % (container_name, labname))
        return
    '''
    # Change <labname>.grades.json ownership to defined user $USER
    command = "sudo chown %s:%s /home/%s/%s/%s.grades.json" % (username, username, username, host_home_xfer, labname)
    logger.DEBUG("Command to execute is (%s)" % command)
    result = subprocess.call(command, shell=True)
    logger.DEBUG("Result of subprocess.Popen exec chown %s.grades.json file is %s" % (labname, result))
    if result == FAILURE:
        StopMyContainer(mycwd, start_config, container_name, ignore_stop_error)
        if ignore_stop_error:
            logger.DEBUG("Container %s fail on executing chown %s.grades.json file!\n" % (container_name, labname))
        else:
            logger.ERROR("Container %s fail on executing chown %s.grades.json file!\n" % (container_name, labname))
        sys.exit(1)
    '''

def StartLab(lab_path, role, is_regress_test=None, force_build=False, is_redo=False, quiet_start=False):
    labname = os.path.basename(lab_path)
    mycwd = os.getcwd()
    myhomedir = os.environ['HOME']
    logger.DEBUG("current working directory for %s" % mycwd)
    logger.DEBUG("current user's home directory for %s" % myhomedir)
    logger.DEBUG("ParseStartConfig for %s" % labname)
    is_valid_lab(lab_path)
    config_path       = os.path.join(lab_path,"config") 
    start_config_path = os.path.join(config_path,"start.config")
   
    start_config = ParseStartConfig.ParseStartConfig(start_config_path, labname, role, logger)
    labtainer_config_dir = os.path.join(os.path.dirname(os.path.dirname(lab_path)), 'config', 'labtainer.config')
    labtainer_config = ParseLabtainerConfig.ParseLabtainerConfig(labtainer_config_dir, logger)
    host_home_xfer = os.path.join(labtainer_config.host_home_xfer, labname)

    build_student = 'bin/buildImage.sh'
    build_instructor = 'bin/buildInstructorImage.sh'
    LABS_DIR = os.path.abspath('../../labs')
    didfix = False
    ''' hackey assumption about running from labtainers-student or labtainers-instructor '''
    container_bin = './bin'
    for name, container in start_config.containers.items():
        mycontainer_name       = container.full_name
        mycontainer_image_name = container.image_name
        if is_redo:
            # If it is a redo then always remove any previous container
            # If it is not a redo, i.e., start.py then DO NOT remove existing container
            cmd = 'docker rm %s' % mycontainer_name
            ps = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            output = ps.communicate()
            logger.DEBUG("Command was (%s)" % cmd)
            if len(output[1]) > 0:
                logger.DEBUG("Error from command = '%s'" % str(output[1]))
        image_exists, result = ImageExists(mycontainer_image_name, mycontainer_name)
        if not image_exists:
            if os.path.isfile(build_student):
                cmd = '%s %s %s %s %s %s %s %s' % (build_student, labname, name, container.user, container.password, False, 
                                                  LABS_DIR, labtainer_config.apt_source)
            elif os.path.isfile(build_instructor):
                cmd = '%s %s %s %s %s %s %s %s' % (build_instructor, labname, name, container.user, container.password, False, 
                                                  LABS_DIR, labtainer_config.apt_source)
            else:
                logger.ERROR("no image rebuild script\n")
                exit(1)
                    
            if os.system(cmd) != 0:
                logger.ERROR("build of image failed\n")
                exit(1)

    # Check existence of /home/$USER/$HOST_HOME_XFER directory - create if necessary
    host_xfer_dir = '%s/%s' % (myhomedir, host_home_xfer)
    CreateHostHomeXfer(host_xfer_dir)

    DoStart(start_config, labtainer_config, lab_path, role, is_regress_test, quiet_start)

def FileModLater(ts, fname):
    ''' is the given file later than the timestamp (which is in UTC)? '''
    df_time = os.path.getmtime(fname)
    #logger.DEBUG('df ts %s' % df_time)

    df_string = datetime.datetime.fromtimestamp(df_time)
    #logger.DEBUG('df_local time is %s' % df_string)

    df_utc_string = str(datetime.datetime.utcfromtimestamp(df_time))
    parts = df_utc_string.split('.')
    df_ts = time.mktime(time.strptime(parts[0], "%Y-%m-%d %H:%M:%S"))

    #logger.DEBUG('df_utc time is %s' % df_utc_string)
    #logger.DEBUG('df_utc ts is %s given ts is %s' % (df_ts, ts))
    if df_ts > ts:
        return True
    else:
        return False

def CheckBuild(lab_path, image_name, container_name, name, role, is_redo, container_bin,
                 grade_container):
    '''
    Determine if a container image needs to be rebuilt.
    '''
    labname = os.path.basename(lab_path)
    should_be_exec = ['rc.local', 'fixlocal.sh']
    retval = False

    image_exists, result = ImageExists(image_name, container_name)
    if image_exists and not is_redo:
        logger.DEBUG('Container %s image %s exists, not a redo, just return (no need to check build)' % (container_name, image_name))
        return False
    elif not image_exists:
        if result is None:
            logger.DEBUG('No image, do rebuild');
        else:
            logger.DEBUG('Image query error %s' % result)
        return True 
    parts = result.strip().split('.')
    time_string = parts[0]
    logger.DEBUG('image time string %s' % time_string)

    ''' ts is the timestamp of the image '''
    ts = time.mktime(time.strptime(time_string, "%Y-%m-%dT%H:%M:%S"))
    logger.DEBUG('image ts %s' % ts)

    ''' look at dockerfiles '''
    df_name = 'Dockerfile.%s' % container_name
    df = os.path.join(lab_path, 'dockerfiles', df_name)
    if not os.path.isfile(df):
         df = df.replace('instructor', 'student')
    if FileModLater(ts, df):
        logger.WARNING('dockerfile changed, will build')
        retval = True
    else:
        ''' look for new/deleted files in the container '''
        container_dir = os.path.join(lab_path, name)
        logger.DEBUG('container dir %s' % container_dir)
        if FileModLater(ts, container_dir):
           logger.WARNING('%s is later, will build' % container_dir)
           retval = True
        else:
            ''' look at all files in container '''
            for folder, subs, files in os.walk(container_dir):
                if os.path.basename(folder) == 'docs':
                    continue
                for f in files:
                   f_path = os.path.join(folder, f)
                   logger.DEBUG('check %s' % f_path)
                   if f in should_be_exec:
                       f_stat = os.stat(f_path)
                       if not f_stat.st_mode & stat.S_IXUSR:
                           response = raw_input("WARNING: not executable: %s\npress enter" % f_path)

                   if FileModLater(ts, f_path):
                       logger.WARNING('%s is later, will build' % f_path)
                       retval = True
                       break


    if not retval:
        param_file = os.path.join(lab_path, 'config', 'parameter.config')
        if os.path.isfile(param_file):
            ppath = 'lab_bin/ParameterParser.py'
            if role == 'instructor':
                ppath = '../labtainer-student/%s' % ppath
            if FileModLater(ts, param_file) or FileModLater(ts, ppath):
              with open(param_file) as param_fh:
                for line in param_fh:
                    if container_name in line or (role == 'instructor' and not line.startswith('#')): 
                        logger.WARNING('%s (or the script) is later and %s mentioned in it, will build' % (param_file, container_name))
                        retval = True
                        break

    if not retval:
        all_bin_files = os.listdir(container_bin)
        for f in all_bin_files:
            f_path = os.path.join(container_bin, f)
            if FileModLater(ts, f_path):
               logger.WARNING('%s is later, will build' % f_path)
               retval = True
               break

    if not retval and role == 'instructor':
        if container_name == grade_container:
            inst_cfg = os.path.join(lab_path,'instr_config')
            inst_cfg_files = os.listdir(inst_cfg)
            for f in inst_cfg_files:
                f_path = os.path.join(inst_cfg, f)
                if FileModLater(ts, f_path):
                   logger.WARNING('%s is later, will build' % f_path)
                   retval = True
                   break
        logger.DEBUG('is instructor')

    logger.DEBUG('returning retaval of %s' % str(retval))    
    return retval

def dumb():
    pass
    '''
    '''
def RedoLab(lab_path, role, is_regress_test=None, force_build=False, quiet_start=False):
    mycwd = os.getcwd()
    myhomedir = os.environ['HOME']
    # Pass 'True' to ignore_stop_error (i.e., ignore certain error encountered during StopLab
    #                                         since it might not even be an error)
    StopLab(lab_path, role, True, is_regress_test)
    is_redo = True
    StartLab(lab_path, role, is_regress_test, force_build, is_redo=is_redo, quiet_start=quiet_start)

def CheckShutdown(lab_path, name, container_name, container_user, ignore_stop_error):
    ''' NOT USED at the moment '''
    done = False
    count = 0
    while not done:
        command='docker cp %s:/tmp/.shutdown_done /tmp/' % (container_name)
        logger.DEBUG(command)
        child = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        error = child.stderr.read().strip()
        if len(error) > 0:
           logger.DEBUG("response from docker cp %s" % error)
           time.sleep(1)
        else:
           logger.DEBUG("must have found the shutdown_done file")
           done = True
        count += 1
        if count > 5:
           done = True

def GatherOtherArtifacts(lab_path, name, container_name, container_user, container_password, ignore_stop_error):
    '''
    Parse the results.config file looking for files named by absolute paths,
    and copy those into the .local/result directory, maintaining the original
    directory structure, e.g., .local/result/var/log/foo.log
    '''
    config_path       = os.path.join(lab_path,"instr_config") 
    results_config_path = os.path.join(config_path,"results.config")
    did_file = []
    CopyAbsToResult(container_name, '/root/.bash_history', container_user, ignore_stop_error) 
    did_file.append('/root/.bash_history')
    with open (results_config_path) as fh:
        for line in fh:
            ''' container:filename is between "=" and first " : " '''
            line = line.strip()
            if line.startswith('#') or len(line) == 0:
                continue
            if '=' not in line:
                logger.WARNING('no = in line %s' % line)
                continue
            after_equals = line.split('=', 1)[1].strip()
            fname = after_equals.split(' : ')[0].strip()
            is_mine = False
            if ':' in fname:
                f_container, fname = fname.split(':')
                logger.DEBUG('f_container <%s> container_name %s' % (f_container, name))
                if f_container.strip() == name:
                    is_mine = True 
                fname = fname.strip()
            else: 
                is_mine = True
            if is_mine:
                logger.DEBUG('file on this container to copy <%s>' % fname )
                if fname.startswith('/') and fname not in did_file:
                    ''' copy from abs path to ~/.local/result ''' 
                    CopyAbsToResult(container_name, fname, container_user, ignore_stop_error) 
                    did_file.append(fname)
                        
def CopyAbsToResult(container_name, fname, container_user, ignore_stop_error):
    ''' copy from abs path to ~/.local/result '''

    #command='docker exec %s echo "%s\n" | sudo -S cp --parents %s /home/%s/.local/result' % (container_name, 
    #    container_password, fname, container_user)
    command='docker exec %s sudo  cp --parents %s /home/%s/.local/result' % (container_name, fname, container_user)
    logger.DEBUG(command)
    child = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    error = child.stderr.read().strip()
    if len(error) > 0:
        if ignore_stop_error:
            logger.DEBUG('error from docker: %s' % error)
            logger.DEBUG('command was %s' % command)
        else:
            logger.DEBUG('error from docker: %s' % error)
            logger.DEBUG('command was %s' % command)
    #command='docker exec %s echo "%s\n" | sudo -S chmod a+r -R /home/%s/.local/result' % (container_name, container_password, container_user)
    command='docker exec %s sudo chmod a+r -R /home/%s/.local/result' % (container_name, container_user)
    child = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    error = child.stderr.read().strip()
    if len(error) > 0:
        if ignore_stop_error:
            logger.DEBUG('chmod ERROR: %s' % error)
            logger.DEBUG('command was %s' % command)
        else:
            logger.ERROR('chmod ERROR: %s' % error)
            logger.ERROR('command was %s' % command)

# RunInstructorCreateGradeFile
def RunInstructorCreateGradeFile(container_name, container_user, labname, is_regress_test):
    # Run 'instructor.py' - This will create '<labname>.grades.txt' 
    logger.DEBUG("About to call instructor.py container_name: %s container_user: %s" % (container_name, container_user))
    cmd_path = '/home/%s/.local/bin/instructor.py' % (container_user)
    if is_regress_test:
        regression_testing = "True"
    else:
        regression_testing = "False"
    command=['docker', 'exec', '-i',  container_name, cmd_path, regression_testing]
    logger.DEBUG('cmd: %s' % str(command))
    child = subprocess.Popen(command, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    error_string = child.stderr.read().strip()
    if len(error_string) > 0:
        logger.ERROR("Container %s fail on executing instructor.py: %s \n" % (container_name, error_string))
    output_string = child.stdout.read().strip()
    if len(output_string) > 0:
        logger.DEBUG("result from container %s executing instructor.py: %s \n" % (container_name, output_string))

def WatermarkTest(lab_path, role, standard, isFirstRun=False):
    labname = os.path.basename(lab_path)
    username = getpass.getuser()
    mycwd = os.getcwd()
    myhomedir = os.environ['HOME']
    logger.DEBUG("ParseStartConfig for %s" % labname)
    is_valid_lab(lab_path)
    config_path       = os.path.join(lab_path,"config") 
    start_config_path = os.path.join(config_path,"start.config")
    start_config = ParseStartConfig.ParseStartConfig(start_config_path, labname, role, logger)

    labtainer_config_dir = os.path.join(os.path.dirname(os.path.dirname(lab_path)), 'config', 'labtainer.config')
    labtainer_config = ParseLabtainerConfig.ParseLabtainerConfig(labtainer_config_dir, logger)
    watermarktest_lab_path = os.path.join(labtainer_config.watermark_root, labname, standard)
    host_home_xfer = os.path.join(labtainer_config.host_home_xfer, labname)
    logger.DEBUG("Host Xfer directory for labname %s is %s" % (labname, host_home_xfer))
    logger.DEBUG("Watermark Test path for labname %s is %s" % (labname, watermarktest_lab_path))

    GradesGold = "%s/%s.grades.txt" % (watermarktest_lab_path, labname)
    Grades = "/home/%s/%s/%s.grades.txt" % (username, host_home_xfer, labname)
    logger.DEBUG("GradesGold is %s - Grades is %s" % (GradesGold, Grades))

    is_regress_test = None
    if isFirstRun:   
	RedoLab(lab_path, role, is_regress_test)
    else: 
	StartLab(lab_path, role, is_regress_test, is_redo=True)

    for name, container in start_config.containers.items():
        mycontainer_name       = container.full_name
        mycontainer_image_name = container.image_name
        container_user         = container.user

        if mycontainer_name == start_config.grade_container:
            logger.DEBUG('about to RunInstructorCreateDradeFile for container %s' % start_config.grade_container)
            RunInstructorCreateGradeFile(start_config.grade_container, container_user, labname, is_regress_test)

    # Pass 'False' to ignore_stop_error (i.e., do not ignore error)
    result_xfer = StopLab(lab_path, role, False, is_regress_test)
    logger.DEBUG('result_xfer is %s' % result_xfer)

    # Give the container some time to copy the result out -- just in case
    time.sleep(3)

    CompareResult = False
    # GradesGold and Grades must exist
    logger.DEBUG('compare %s to %s' % (GradesGold, Grades))
    if not os.path.exists(GradesGold):
        logger.ERROR("GradesGold %s file does not exist!" % GradesGold)
    elif not os.path.exists(Grades):
        logger.ERROR("Grades %s file does not exist!" % Grades)
    else:
        CompareResult = filecmp.cmp(GradesGold, Grades)
    return CompareResult


def RegressTest(lab_path, role, standard, isFirstRun=False):
    labname = os.path.basename(lab_path)
    username = getpass.getuser()
    mycwd = os.getcwd()
    myhomedir = os.environ['HOME']
    logger.DEBUG("ParseStartConfig for %s" % labname)
    is_valid_lab(lab_path)
    config_path       = os.path.join(lab_path,"config") 
    start_config_path = os.path.join(config_path,"start.config")
    start_config = ParseStartConfig.ParseStartConfig(start_config_path, labname, role, logger)

    labtainer_config_dir = os.path.join(os.path.dirname(os.path.dirname(lab_path)), 'config', 'labtainer.config')
    labtainer_config = ParseLabtainerConfig.ParseLabtainerConfig(labtainer_config_dir, logger)
    regresstest_lab_path = os.path.join(labtainer_config.testsets_root, labname, standard)
    host_home_xfer = os.path.join(labtainer_config.host_home_xfer, labname)
    logger.DEBUG("Host Xfer directory for labname %s is %s" % (labname, host_home_xfer))
    logger.DEBUG("Regression Test path for labname %s is %s" % (labname, regresstest_lab_path))

    GradesGold = "%s/%s.grades.txt" % (regresstest_lab_path, labname)
    Grades = "/home/%s/%s/%s.grades.txt" % (username, host_home_xfer, labname)
    logger.DEBUG("GradesGold is %s - Grades is %s" % (GradesGold, Grades))

    is_regress_test = standard
    if isFirstRun:   
	RedoLab(lab_path, role, is_regress_test)
    else: 
	StartLab(lab_path, role, is_regress_test, is_redo=True)

    for name, container in start_config.containers.items():
        mycontainer_name       = container.full_name
        mycontainer_image_name = container.image_name
        container_user         = container.user

        if mycontainer_name == start_config.grade_container:
            logger.DEBUG('about to RunInstructorCreateDradeFile for container %s' % start_config.grade_container)
            RunInstructorCreateGradeFile(start_config.grade_container, container_user, labname, is_regress_test)

    # Pass 'False' to ignore_stop_error (i.e., do not ignore error)
    result_xfer = StopLab(lab_path, role, False, is_regress_test)
    logger.DEBUG('result_xfer is %s' % result_xfer)

    # Give the container some time to copy the result out -- just in case
    time.sleep(3)

    CompareResult = False
    # GradesGold and Grades must exist
    logger.DEBUG('compare %s to %s' % (GradesGold, Grades))
    if not os.path.exists(GradesGold):
        logger.ERROR("GradesGold %s file does not exist!" % GradesGold)
    elif not os.path.exists(Grades):
        logger.ERROR("Grades %s file does not exist!" % Grades)
    else:
        CompareResult = filecmp.cmp(GradesGold, Grades)
    return CompareResult


def CreateCopyChownZip(mycwd, start_config, labtainer_config, container_name, container_image, container_user, container_password, ignore_stop_error):
    '''
    Zip up the student home directory and copy it to the Linux host home directory
    '''
    logger.DEBUG('in CreateCopyChownZip')
    host_home_xfer  = os.path.join(labtainer_config.host_home_xfer, start_config.labname)
    lab_master_seed = start_config.lab_master_seed

    # Run 'Student.py' - This will create zip file of the result
    logger.DEBUG("About to call Student.py")
    cmd_path = '/home/%s/.local/bin/Student.py' % (container_user)
    #command=['docker', 'exec', '-i',  container_name, 'echo "%s\n" |' % container_password, '/usr/bin/sudo', cmd_path, container_user, container_image]
    command=['docker', 'exec', '-i',  container_name, '/usr/bin/sudo', cmd_path, container_user, container_image]
    logger.DEBUG('cmd: %s' % str(command))
    child = subprocess.Popen(command, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    error_string = child.stderr.read().strip()
    if len(error_string) > 0:
        if ignore_stop_error:
            logger.DEBUG("Container %s fail on executing Student.py \n" % (container_name))
        else:
            logger.ERROR("Container %s fail on executing Student.py \n" % (container_name))
        return None, None
    
    #out_string = output[0].strip()
    #if len(out_string) > 0:
    #    logger.DEBUG('output of Student.py is %s' % out_string)
    username = getpass.getuser()

    tmp_dir=os.path.join('/tmp/labtainers', container_name)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    try:
        os.makedirs(tmp_dir)
    except:
        logger.ERROR("did not expect to find dir %s" % tmp_dir)
    source_dir = os.path.join('/home', container_user, '.local', 'zip')
    cont_source = '%s:%s' % (container_name, source_dir)
    logger.DEBUG('will copy from %s ' % source_dir)
    command = ['docker', 'cp', cont_source, tmp_dir]
    # The zip filename created by Student.py has the format of e-mail.labname.zip
    logger.DEBUG("Command to execute is (%s)" % command)
    child = subprocess.Popen(command, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    error_string = child.stderr.read().strip()
    if len(error_string) > 0:
        if ignore_stop_error:
            logger.DEBUG("Container %s fail on executing cp zip file!\n" % container_name)
            logger.DEBUG("Command was (%s)" % command)
        else:
            logger.ERROR("Container %s fail on executing cp zip file!\n" % container_name)
            logger.ERROR("Command was (%s)" % command)
        StopMyContainer(mycwd, start_config, container_name, ignore_stop_error)
        return None, None
    
    local_tmp_zip = os.path.join(tmp_dir, 'zip')
    try:
        orig_zipfilenameext = os.listdir(local_tmp_zip)[0]
    except:
        if ignore_stop_error:
            logger.DEBUG('no files at %s\n' % local_tmp_zip)
        else:
            logger.ERROR('no files at %s\n' % local_tmp_zip)
        return None, None
    orig_zipfilename, orig_zipext = os.path.splitext(orig_zipfilenameext)
    baseZipFilename = os.path.basename(orig_zipfilename)
    #NOTE: Use the '=' to separate e-mail+labname from the container_name
    DestZipFilename = '%s=%s.zip' % (baseZipFilename, container_name)
    DestZipPath = os.path.join('/home', username, host_home_xfer, DestZipFilename)
    shutil.copyfile(os.path.join(local_tmp_zip, orig_zipfilenameext), DestZipPath)

    '''
    # Change ownership to defined user $USER
    command = "sudo chown %s:%s /home/%s/%s/*.zip" % (username, username, username, host_home_xfer)
    logger.DEBUG("Command to execute is (%s)" % command)
    child = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    error_string = child.stderr.read().strip()
    if len(error_string) > 0:
        if ignore_stop_error:
            logger.DEBUG("chown failed Command was (%s)" % command)
            logger.DEBUG("Container %s fail on executing chown zip file!\n" % container_name)
        else:
            logger.ERROR("chown failed Command was (%s)" % command)
            logger.ERROR("Container %s fail on executing chown zip file!\n" % container_name)
        StopMyContainer(mycwd, start_config, container_name, ignore_stop_error)
        return None, None

    '''
    currentContainerZipFilename = "/home/%s/%s/%s" % (username, host_home_xfer, DestZipFilename)
    return baseZipFilename, currentContainerZipFilename
   
# Stop my_container_name container
def StopMyContainer(mycwd, start_config, container_name, ignore_stop_error):
    command = "docker stop -t 1 %s 2>/dev/null" % container_name
    logger.DEBUG("Command to execute is (%s)" % command)
    ps = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    output = ps.communicate()
    if len(output[1].strip()) > 0:
        if ignore_stop_error:
            logger.DEBUG('Fail to stop container, error returned %s' % output[1])
        else:
            logger.ERROR('Fail to stop container, error returned %s' % output[1])
    #if len(output[0].strip()) > 0:
    #    logger.DEBUG('StopMyContainer stdout %s' % output[0])
    #result = subprocess.call(command, shell=True)

def IsContainerRunning(mycontainer_name):
    try:
        s = subprocess.check_output('docker ps', shell=True)
    except:
        return False
    if mycontainer_name in s:
        return True
    else:
        return False 

def DoStopOne(start_config, labtainer_config, mycwd, lab_path, role, name, container, ZipFileList, ignore_stop_error, results):
        labname = os.path.basename(lab_path) 
        #dumlog = os.path.join('/tmp', name+'.log')
        #sys.stdout = open(dumlog, 'w')
        #sys.stderr = sys.stdout
        retval = True
        mycontainer_name  = container.full_name
        container_user    = container.user
        container_password    = container.password
        mycontainer_image = container.image_name
        haveContainer     = IsContainerCreated(mycontainer_name)
        logger.DEBUG("IsContainerCreated result (%s)" % haveContainer)

        # IsContainerCreated returned FAILURE if container does not exists
        # error: can't stop non-existent container
        if not haveContainer:
            if ignore_stop_error:
                logger.DEBUG("Container %s does not exist!\n" % mycontainer_name)
            else:
                logger.ERROR("Container %s does not exist!\n" % mycontainer_name)
            retval = False
        elif not IsContainerRunning(mycontainer_name):
            if ignore_stop_error:
                logger.DEBUG("container %s not running\n" % (mycontainer_name))
            else:
                logger.ERROR("container %s not running\n" % (mycontainer_name))
            retval = False
        else:
            if role == 'instructor':
                if mycontainer_name == start_config.grade_container:
                    CopyChownGradesFile(mycwd, start_config, labtainer_config, mycontainer_name, mycontainer_image, container_user, ignore_stop_error)
            else:
                GatherOtherArtifacts(lab_path, name, mycontainer_name, container_user, container_password, ignore_stop_error)
                # Before stopping a container, run 'Student.py'
                # This will create zip file of the result
    
                baseZipFilename, currentContainerZipFilename = CreateCopyChownZip(mycwd, start_config, labtainer_config, mycontainer_name, mycontainer_image, container_user, container_password, ignore_stop_error)
                if baseZipFilename is not None:
                    ZipFileList.append(currentContainerZipFilename)
                logger.DEBUG("baseZipFilename is (%s)" % baseZipFilename)

            #command = 'docker exec %s echo "%s\n" | sudo -S rmdir /tmp/.mylockdir 2>/dev/null' % (mycontainer_name, container_password)
            command = 'docker exec %s sudo rmdir /tmp/.mylockdir 2>/dev/null' % (mycontainer_name)
            os.system(command)

            for mysubnet_name, mysubnet_ip in container.container_nets.items():
                disconnectNetworkResult = DisconnectNetworkFromContainer(mycontainer_name, mysubnet_name)

            # Stop the container
            StopMyContainer(mycwd, start_config, mycontainer_name, ignore_stop_error)

        results.append(retval)

def DoStop(start_config, labtainer_config, mycwd, lab_path, role, ignore_stop_error, is_regress_test=None):
    retval = True
    labname = os.path.basename(lab_path)
    host_home_xfer  = os.path.join(labtainer_config.host_home_xfer, labname)
    lab_master_seed = start_config.lab_master_seed
    logger.DEBUG("DoStop Multiple Containers and/or multi-home networking")

    username = getpass.getuser()

    baseZipFilename = ""
    ZipFileList = []
    threads = []
    results = []
    for name, container in start_config.containers.items():
        mycontainer_name = '%s.%s.%s' % (labname, container.name, role)
        if is_regress_test and mycontainer_name != start_config.grade_container:
            #print('compare %s to %s' % (mycontainer_name, start_config.grade_container))
            continue

        #DoStopOne(start_config, labtainer_config, mycwd, labname, role, name, container, ZipFileList)
        t = threading.Thread(target=DoStopOne, args=(start_config, labtainer_config, mycwd, lab_path, 
              role, name, container, ZipFileList, ignore_stop_error, results))
        threads.append(t)
        t.setName(name)
        t.start()
      
    logger.DEBUG('started all')
    for t in threads:
        t.join()
        logger.DEBUG('joined %s' % t.getName())

    if not ignore_stop_error:
        if False in results:
            logger.ERROR('DoStopOne has at least one failure!')
            sys.exit(1)

    RemoveSubnets(start_config.subnets, ignore_stop_error)
    if role == 'student':
        if len(ZipFileList) == 0:
            if ignore_stop_error:
                logger.DEBUG('No zip files found')
            else:
                logger.ERROR('No zip files found')
            return None
        base_filename = os.path.basename(ZipFileList[0])
        baseZipFilename = base_filename.split('=')[0]

        xfer_dir = "/home/%s/%s" % (username, host_home_xfer)

        # Create docs.zip in xfer_dir if COLLECT_DOCS is "yes"
        if start_config.collect_docs.lower() == "yes":
            docs_zip_file = "%s/docs.zip" % xfer_dir
            logger.DEBUG("Zipping docs directory to %s" % docs_zip_file)

            docs_path = '%s/docs' % lab_path
            docs_zip_filelist = glob.glob('%s/*' % docs_path)
            logger.DEBUG(docs_zip_filelist)

            # docs.zip file
            docs_zipoutput = zipfile.ZipFile(docs_zip_file, "w")
            # Go to the docs_path
            os.chdir(docs_path)
            for docs_fname in docs_zip_filelist:
                docs_basefname = os.path.basename(docs_fname)
                docs_zipoutput.write(docs_basefname, compress_type=zipfile.ZIP_DEFLATED)
                # Note: DO NOT remove after the file is zipped
            docs_zipoutput.close()

            # Add docs.zip into the ZipFileList
            ZipFileList.append(docs_zip_file)

        # Combine all the zip files
        logger.DEBUG("ZipFileList is ")
        logger.DEBUG(ZipFileList)
        logger.DEBUG("baseZipFilename is (%s)" % baseZipFilename)
        combinedZipFilename = "%s/%s.zip" % (xfer_dir, baseZipFilename)
        logger.DEBUG("The combined zip filename is %s" % combinedZipFilename)
        zipoutput = zipfile.ZipFile(combinedZipFilename, "w")
        # Go to the xfer_dir
        os.chdir(xfer_dir)
        for fname in ZipFileList:
            basefname = os.path.basename(fname)
            zipoutput.write(basefname, compress_type=zipfile.ZIP_DEFLATED)
            # Remove after the file is zipped
            os.remove(basefname)
        zipoutput.close()

    os.chdir(mycwd)
    return retval

# ignore_stop_error - set to 'False' : do not ignore error
# ignore_stop_error - set to 'True' : ignore certain error encountered since it might not even be an error
#                                     such as error encountered when trying to stop non-existent container
def StopLab(lab_path, role, ignore_stop_error, is_regress_test=None):
    labname = os.path.basename(lab_path)
    mycwd = os.getcwd()
    myhomedir = os.environ['HOME']
    logger.DEBUG("current working directory for %s" % mycwd)
    logger.DEBUG("current user's home directory for %s" % myhomedir)
    logger.DEBUG("ParseStartConfig for %s" % labname)
    is_valid_lab(lab_path)
    config_path       = os.path.join(lab_path,"config") 
    start_config_path = os.path.join(config_path,"start.config")
   
    start_config = ParseStartConfig.ParseStartConfig(start_config_path, labname, role, logger)
    labtainer_config_dir = os.path.join(os.path.dirname(os.path.dirname(lab_path)), 'config', 'labtainer.config')
    labtainer_config = ParseLabtainerConfig.ParseLabtainerConfig(labtainer_config_dir, logger)
    host_home_xfer = os.path.join(labtainer_config.host_home_xfer, labname)

    # Check existence of /home/$USER/$HOST_HOME_XFER directory - create if necessary
    host_xfer_dir = '%s/%s' % (myhomedir, host_home_xfer)
    CreateHostHomeXfer(host_xfer_dir)

    if DoStop(start_config, labtainer_config, mycwd, lab_path, role, ignore_stop_error, is_regress_test):
        # Inform user where results are stored
        print "Results stored in directory: %s" % host_xfer_dir
    return host_xfer_dir

def DoMoreterm(lab_path, role, container, num_terminal):
    labname = os.path.basename(lab_path)
    mycwd = os.getcwd()
    myhomedir = os.environ['HOME']
    logger.DEBUG("current working directory for %s" % mycwd)
    logger.DEBUG("current user's home directory for %s" % myhomedir)
    logger.DEBUG("ParseStartConfig for %s" % labname)
    is_valid_lab(lab_path)
    config_path       = os.path.join(lab_path,"config")
    start_config_path = os.path.join(config_path,"start.config")

    start_config = ParseStartConfig.ParseStartConfig(start_config_path, labname, role, logger)
    logger.DEBUG('num terms is %d' % start_config.containers[container].terminals)

    mycontainer_name = '%s.%s.%s' % (labname, container, role)
    if not IsContainerCreated(mycontainer_name):
        logger.ERROR('container %s not found' % mycontainer_name)
        sys.exit(1)
    if not IsContainerRunning(mycontainer_name):
        logger.ERROR("Container %s is not running!\n" % (mycontainer_name))
        sys.exit(1)
    for x in range(num_terminal):
	if start_config.containers[container].terminals == 0:
            print("No terminals supported for this component")
	    sys.exit(1)
	else:
            spawn_command = "gnome-terminal -- docker exec -it %s bash -l &" % 	mycontainer_name
	    logger.DEBUG("spawn_command is (%s)" % spawn_command)
	    os.system(spawn_command)

def DoTransfer(lab_path, role, container, filename, direction):
    labname = os.path.basename(lab_path)
    mycwd = os.getcwd()
    myhomedir = os.environ['HOME']
    logger.DEBUG("current working directory for %s" % mycwd)
    logger.DEBUG("current user's home directory for %s" % myhomedir)
    logger.DEBUG("ParseStartConfig for %s" % labname)
    is_valid_lab(lab_path)
    config_path       = os.path.join(lab_path,"config")
    start_config_path = os.path.join(config_path,"start.config")

    start_config = ParseStartConfig.ParseStartConfig(start_config_path, labname, role, logger)
    labtainer_config_dir = os.path.join(os.path.dirname(os.path.dirname(lab_path)), 'config', 'labtainer.config')
    labtainer_config = ParseLabtainerConfig.ParseLabtainerConfig(labtainer_config_dir, logger)
    host_home_xfer = os.path.join(labtainer_config.host_home_xfer, labname)
    logger.DEBUG('num terms is %d' % start_config.containers[container].terminals)
    host_xfer_dir = '%s/%s' % (myhomedir, host_home_xfer)

    mycontainer_name = '%s.%s.%s' % (labname, container, role)
    if not IsContainerCreated(mycontainer_name):
        logger.ERROR('container %s not found' % mycontainer_name)
        sys.exit(1)
    if not IsContainerRunning(mycontainer_name):
        logger.ERROR("Container %s is not running!\n" % (mycontainer_name))
        sys.exit(1)
    container_user = ""
    for name, container in start_config.containers.items():
        if mycontainer_name == container.full_name:
            container_user = container.user

    if direction == "TOCONTAINER":
        # Transfer from host to container
        filename_path = '%s/%s' % (host_xfer_dir, filename)
        logger.DEBUG("File to transfer from host is (%s)" % filename_path)
        if os.path.exists(filename_path) and os.path.isfile(filename_path):
            # Copy file and chown it
            command = 'docker cp %s %s:/home/%s/' % (filename_path, mycontainer_name, container_user)
            logger.DEBUG("Command to execute is (%s)" % command)
            result = subprocess.call(command, shell=True)
            logger.DEBUG("Result of subprocess.call DoTransfer copy (TOCONTAINER) file (%s) is %s" % (filename_path, result))
            if result == FAILURE:
                logger.ERROR("Failed to copy file to container %s!\n" % mycontainer_name)
                sys.exit(1)
            command = 'docker exec %s sudo chown %s:%s /home/%s/%s' % (mycontainer_name, container_user, container_user, container_user, filename)
            logger.DEBUG("Command to execute is (%s)" % command)
            result = subprocess.call(command, shell=True)
            logger.DEBUG("Result of subprocess.call DoTransfer chown file (%s) is %s" % (filename_path, result))
            if result == FAILURE:
                logger.ERROR("Failed to set permission in container %s!\n" % mycontainer_name)
                sys.exit(1)
        else:
            logger.ERROR('Host does not have %s file' % filename_path)
	    sys.exit(1)
    else:
        # Transfer from container to host
        command = 'docker cp %s:/home/%s/%s %s/' % (mycontainer_name, container_user, filename, host_xfer_dir)
        logger.DEBUG("Command to execute is (%s)" % command)
        result = subprocess.call(command, shell=True)
        logger.DEBUG("Result of subprocess.call DoTransfer copy (TOHOST) file (%s) is %s" % (filename, result))
        if result == FAILURE:
            logger.ERROR("Failed to copy file from container %s!\n" % mycontainer_name)
            sys.exit(1)

def DoPauseorUnPause(lab_path, role, command_desired):
    labname = os.path.basename(lab_path)
    mycwd = os.getcwd()
    myhomedir = os.environ['HOME']
    logger.DEBUG("current working directory for %s" % mycwd)
    logger.DEBUG("current user's home directory for %s" % myhomedir)
    logger.DEBUG("ParseStartConfig for %s" % labname)
    is_valid_lab(lab_path)
    config_path       = os.path.join(lab_path,"config")
    start_config_path = os.path.join(config_path,"start.config")

    start_config = ParseStartConfig.ParseStartConfig(start_config_path, labname, role, logger)

    error_encountered = False
    for name, container in start_config.containers.items():
        mycontainer_name       = container.full_name
        mycontainer_image_name = container.image_name
        container_user         = container.user

        if not IsContainerCreated(mycontainer_name):
            logger.ERROR('container %s not found' % mycontainer_name)
            error_encountered = True
            continue
        if not IsContainerRunning(mycontainer_name):
            logger.ERROR("Container %s is not running!\n" % mycontainer_name)
            error_encountered = True
            continue

        if command_desired == "pause":
            logger.DEBUG("command_desired pause")
        elif command_desired == "unpause":
            logger.DEBUG("command_desired unpause")
        else:
            logger.ERROR("Invalid command_desired %s" % command_desired)
            error_encountered = True
            break

        command = "docker %s %s" % (command_desired, mycontainer_name)
        logger.DEBUG("command is (%s)" % command)
        os.system(command)

    if error_encountered:
        logger.ERROR("One of more error encountered during %s container!\n" % command_desired)

def GetDNS():
    #command = 'nmcli dev show | grep DNS'
    retval = []
    command = 'nmcli dev show'
    ps = subprocess.Popen(shlex.split(command), True, stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    grep_command = 'grep DNS'
    ps_grep = subprocess.Popen(shlex.split(grep_command), stdin=ps.stdout,stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ps.stdout.close()
    output = ps_grep.communicate()
    if len(output[0]) > 0:
        for line in output[0].splitlines(True):
            parts = line.split()
            retval.append(parts[1])
    return retval

def CopyFilesToHost(lab_path, container_name, full_container_name, container_user):
    labname = os.path.basename(lab_path)
    is_valid_lab(lab_path)
    config_path       = os.path.join(lab_path,"config") 
    copy_path = os.path.join(config_path,"files_to_host.config")
    logger.DEBUG('CopyFilesToHost %s %s %s' % (labname, container_name, full_container_name))
    logger.DEBUG('CopyFilesToHost copypath %s' % copy_path)
    if os.path.isfile(copy_path):
        with open(copy_path) as fh:
            for line in fh:
                if not line.strip().startswith('#'):
                    try:
                        os.mkdir(os.path.join(os.getcwd(), labname))
                    except OSError as e:
                        #logger.ERROR('could not mkdir %s in %s %s' % (labname, os.getcwd(),str(e)))
                        pass
                    container, file_name = line.split(':')                    
                    if container == container_name:
                        dest = os.path.join(os.getcwd(), labname, file_name)
                        command = 'docker cp %s:/home/%s/%s %s' % (full_container_name, container_user, 
                            file_name.strip(), dest)
                        logger.DEBUG("Command to execute is (%s)" % command)
                        result = subprocess.call(command, shell=True)
                        logger.DEBUG("Result of subprocess.call DoTransfer copy (TOHOST) file (%s) is %s" % (file_name, 
                            result))
                        if result == FAILURE:
                            logger.ERROR("Failed to copy file from container %s!\n" % full_container_name)
                            sys.exit(1)
