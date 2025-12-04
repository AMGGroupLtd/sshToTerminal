# SSH To Terminal Updater

This script is designed to add any SSH client configs to the windows terminal application.

It takes all SSH config files from the user's home directory and adds them to the terminal settings.json file.

It should run under both WSL and Windows.  If run under WSL it will use the current username to try and find the Terminal settings file.

This can be overridden via command parameters.

Command Parameters:

-h --help -> Show this help message and exit.    
-d --debug -> Show debug messages.  
-s --ssh-dir -> The directory to search for SSH config files. (defaults to ~/.ssh and includes all subdirectories).  
-n --nosubdir -> Do not search subdirectories of the SSH directory.  
-t --terminal -> The path to the Windows Terminal settings.json file. (defaults to %LOCALAPPDATA%\Packages\Microsoft.WindowsTerminal_8wekyb3d8bbwe\LocalState\settings.json).  
-a --add -> Add the SSH config files to the terminal settings.json file.  
-r --remove -> Remove the SSH config files from the terminal settings.json file.  
-e --exclude -> Exclude the specified SSH config files from the terminal settings.json file.  

An SSH config file is determined by checking for the presence of the string 'Host' at the start of any line in the file.
All files in the SSH directory are searched for host definitions, unless --nosubdir is specified.  
Each file found is scanned for 1 or more 'Host' lines and a new host is created in settings.json for each found.

For a typical SSH config, the mappings are as follows:

Host Server01
        HostName 1.2.3.4
        User MyUserName
        Port MySshPort
        IdentityFile MyPrivateKeyFile

settings.name = Server01
settings.commandline = ssh -p MySshPort -i MyPrivateKeyFile MyUserName@1.2.3.4
settings.guid = RandomGuid
settings.hidden = false



If settings.name is already present, then it is overridden with the new settings.  
If User is not specified, then 'MyUserName@' is not added to the commandline.  
If Port is not specified, then '-p MySshPort' is not added to the commandline.  
If IdentityFile is not specified, then '-i MyPrivateKeyFile' is not added to the commandline.  

We need to allow for future expansion of the mappings, it may be helpful to add port redirection and the likes at a later date

