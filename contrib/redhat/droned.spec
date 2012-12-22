%if ! (0%{?fedora} > 14 || 0%{?rhel} > 6)
#use classic sysv init scripts
%define systemd 0
%else
#use systemd hotness
%define systemd 1
%endif

%if (0%{?rhel} < 6)
%define ghost_safe 0
%define need_simplejson 1
%else
%define ghost_safe 1
%define need_simplejson 0
%endif

#adding explicit python2 requirement
%{?__python2: %define __python %__python2}

Name:		droned
Version:        0.9.1
Release:	1%{?dist}
Summary:	DroneD - Application Service Framework	

Group:		System Environment/Daemons
License:	ASL 2.0
URL:		https://github.com/OrbitzWorldwide/droned
BuildArch:	noarch
Source0:	%{name}-%{version}.tar
BuildRoot:	%(mktemp -ud %{_tmppath}/%{name}-%{version}-%{release}-XXXXXX)

BuildRequires:	%{__python}
Requires:	python-twisted
Requires:	python-romeo
Requires:	python-ctypes
Requires(post):	openssl


%description
DroneD is a service container geared towards application management and monitoring.
It provides a rich API for implementing services and messaging interfaces. It is 
built on the Twisted Framework, and utilizes the BlasterProtocol and/or XMPP for 
network communication.


%package -n python-romeo
Summary:	Relational Object Mapping of Environmental Organization
Group:		Development/Languages
Requires:	PyYAML
Requires:	python-ctypes
%if %{need_simplejson}
Requires:	python-simplejson
%endif


%description -n python-romeo
Romeo: Relational Object Mapping of Environmental Organization

What is that supposed to mean? Romeo is built to provide programmatic 
access to information about the various components of an environment. What 
kind of components? Servers, applications, dependencies, and anything else you
can describe in terms of Key Value pairs. 

Question:
Isn't this the job of a CMDB?

Answer:
Find me a well documented CMDB API that is also an OSS CMDB that integrates 
well with DroneD and I will happily retire Romeo.


%package -n romeo-utils
Summary:	Command line utilities for manipulating romeo files
Group:		Applications/System
Requires:	python-romeo 
Requires:	python-twisted


%description -n romeo-utils
This utils package installs a number of command line tools for
manipulating romeo environment description files.


%prep
%setup -q


%build
for dir in %{name} romeo
do
  cd $dir
  %{__python} setup.py build
  cd ..
done

%install
for dir in %{name} romeo
do
  cd $dir
  %{__python} setup.py install \
      --root=$RPM_BUILD_ROOT \
      --record=INSTALLED_FILES \
      --optimize=1 #don't bytecode optimize rpmbuild will do it for us
  cd ..
done
#fix for brp_python_bytecompile on some systems
%{__mv} %{name}/INSTALLED_FILES %{name}/INSTALLED_FILES.orig
egrep -v '*.pyo|*.pyc' %{name}/INSTALLED_FILES.orig | \
	sed 's|\(.*.py\)$|\1*|' > \
	%{name}/INSTALLED_FILES

#for ROMEO configuration
%{__mkdir_p} $RPM_BUILD_ROOT%{_sysconfdir}/hostdb
#where to put daemon startup configuration
%{__mkdir_p} $RPM_BUILD_ROOT%{_sysconfdir}/%{name}
#for DroneD RSA keys, logging, and state storage
%{__mkdir_p} $RPM_BUILD_ROOT%{_sysconfdir}/pki/%{name}
%{__mkdir_p} $RPM_BUILD_ROOT/var/lib/%{name}

#make a log dir, can be used by droned's daemon maker
#even if systemd is present on the system.
%{__mkdir_p} $RPM_BUILD_ROOT/var/log/%{name}
%if %{systemd}
#install redhat systemd units
%{__install} -D contrib/redhat/%{name}.service \
	$RPM_BUILD_ROOT/lib/systemd/system/%{name}.service
%{__install} -D contrib/redhat/private-drone.service \
	$RPM_BUILD_ROOT/lib/systemd/system/private-drone.service
%else
#install redhat SysV init settings
%{__install} -D contrib/redhat/%{name}.init \
	$RPM_BUILD_ROOT%{_sysconfdir}/init.d/%{name}
#for now sysv and systemd are using external config for DroneD
%{__install} -D contrib/redhat/%{name}.sys \
	$RPM_BUILD_ROOT%{_sysconfdir}/sysconfig/%{name}
%endif


#write basic start configuration
cat<<EOF_CONF > $RPM_BUILD_ROOT%{_sysconfdir}/%{name}/%{name}.conf
[droned]
homedir = /var/lib/droned
journal = /var/lib/droned/journal
hostdb = /etc/hostdb
rsadir = /etc/pki/droned
logdir = /var/log/droned
primefile = /usr/share/droned/primes
privatekey = local
concurrency =  5
umask = 0
maxfd = 1024
uid = nobody
gid = nobody
wait = 10
deadline = 30
EOF_CONF


%clean
rm -rf $RPM_BUILD_ROOT


%post
#echo "running post" #this runs even on updates :-/
#install (not upgrade)
#local keys should be private on every host, but don't assume everyone
#to have that policy. if the keys are already present, don't regenerate.
cd %{_sysconfdir}/pki/%{name}
if [ -r local.private -a -r local.public ]; then
    true
else   
    /usr/bin/openssl \
        genrsa \
        -out \
        local.private >&/dev/null
    /usr/bin/openssl \
        rsa \
        -in \
        local.private \
        -pubout \
        -out \
        local.public >&/dev/null
fi
%if %{systemd}
# units by default
if [ $1 -eq 1 ] ; then
    # Initial installation
    /bin/systemctl daemon-reload >/dev/null 2>&1 || :
fi
%else
/sbin/chkconfig --add %{name} >&/dev/null || :
%endif


%preun
if [ $1 -eq 0 ] ; then
%if %{systemd}
    # Package removal, not upgrade
    /bin/systemctl --no-reload disable %{name}.service > /dev/null 2>&1 || :
    /bin/systemctl stop %{name}.service > /dev/null 2>&1 || :
    /bin/systemctl --no-reload disable private-drone.service > /dev/null 2>&1 || :
    /bin/systemctl stop private-drone.service > /dev/null 2>&1 || :
%else
    /sbin/service %{name} stop >/dev/null 2>&1 || :
    /sbin/chkconfig %{name} off >&/dev/null || :
    /sbin/chkconfig --del %{name} >&/dev/null || :  
%endif  
fi

%postun
%if %{systemd}
/bin/systemctl daemon-reload >/dev/null 2>&1 || :
if [ $1 -ge 1 ] ; then
    # Package upgrade, not uninstall
    /bin/systemctl try-restart private-drone.service >/dev/null 2>&1 || :
fi
%endif


%files -f %{name}/INSTALLED_FILES
%defattr(-,root,root,-)
%doc LICENSE README NEWS
%dir %attr(755, root, root) %{_sysconfdir}/%{name}
%config(noreplace) %attr(644,root,root) %{_sysconfdir}/%{name}/%{name}.conf
%if %{systemd}
%config(noreplace) %attr(644,root,root) /lib/systemd/system/%{name}.service
%else
%attr(755,root,root) %{_sysconfdir}/init.d/%{name}
%config(noreplace) %attr(644,root,root) %{_sysconfdir}/sysconfig/%{name}
%endif
%dir /var/log/%{name}
%dir %{_sysconfdir}/pki/%{name}
%if %{ghost_safe}
%ghost %attr(600,root,root) %{_sysconfdir}/pki/%{name}/local.private
%ghost %attr(644,root,root) %{_sysconfdir}/pki/%{name}/local.public
%endif
%dir %{_datadir}/%{name}
%dir /var/lib/%{name}


%files -n python-romeo -f romeo/INSTALLED_FILES
%defattr(-,root,root,-)
%doc LICENSE README NEWS romeo/example/*
%dir %{_sysconfdir}/hostdb
%exclude %{_bindir}/rls
%exclude %{_bindir}/createrdb


%files -n romeo-utils
%defattr(-,root,root,-)
%doc LICENSE
%{_bindir}/rls
%{_bindir}/createrdb


%changelog
* Sun Aug 26 2012 Justin Venus <justin.venus@orbitz.com> 0.9.1-1
- Added caching to romeo library
- Added new commandline utility createrdb
- Minor version bump

* Tue Jun  3 2012 Justin Venus <justin.venus@orbitz.com> 0.9.0-9
- Added SystemD (version 183+) WATCHDOG support to droned.

* Wed Apr 11 2012 Justin Venus <justin.venus@orbitz.com> 0.9.0-3
- cleaning up the specfile - jciesla

* Wed Jan 25 2012 Justin Venus <justin.venus@orbitz.com> 0.9.0-1
- getting ready to release open source.
- previous rpm changelog history has been removed to protect the innocent.
