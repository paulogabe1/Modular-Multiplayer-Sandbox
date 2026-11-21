// Copyright Epic Games, Inc. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Character.h"
#include "Logging/LogMacros.h"
#include "Abilities/AbilityTypes.h"
#include "WishCraftCharacter.generated.h"

class USpringArmComponent;
class UCameraComponent;
class UInputAction;
class UHealthComponent;
class UAbilityLoadoutManager;
class UNiagaraComponent;
struct FInputActionValue;
class FLifetimeProperty;
class AWishCraftGameState;
class UAudioComponent;
class USoundBase;
class UNiagaraSystem;

DECLARE_LOG_CATEGORY_EXTERN(LogTemplateCharacter, Log, All);

/**
 *  A simple player-controllable third person character
 *  Implements a controllable orbiting camera
 */
UCLASS(abstract)
class AWishCraftCharacter : public ACharacter
{
	GENERATED_BODY()

	/** Camera boom positioning the camera behind the character */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Components", meta = (AllowPrivateAccess = "true"))
	USpringArmComponent* CameraBoom;

	/** Follow camera */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Components", meta = (AllowPrivateAccess = "true"))
	UCameraComponent* FollowCamera;

	/** Health, used by fireball damage and by the match flow */
	UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category="Components", meta = (AllowPrivateAccess = "true"))
	UHealthComponent* HealthComponent;
	
protected:

	/** Jump Input Action */
	UPROPERTY(EditAnywhere, Category="Input")
	UInputAction* JumpAction;

	/** Move Input Action */
	UPROPERTY(EditAnywhere, Category="Input")
	UInputAction* MoveAction;

	/** Look Input Action */
	UPROPERTY(EditAnywhere, Category="Input")
	UInputAction* LookAction;

	/** Mouse Look Input Action */
	UPROPERTY(EditAnywhere, Category="Input")
	UInputAction* MouseLookAction;

	/** Offense slot ability input */
	UPROPERTY(EditAnywhere, Category="Input|Abilities")
	UInputAction* AbilityOffenseAction;

	/** Defense slot ability input */
	UPROPERTY(EditAnywhere, Category="Input|Abilities")
	UInputAction* AbilityDefenseAction;

	/** Mobility slot ability input */
	UPROPERTY(EditAnywhere, Category="Input|Abilities")
	UInputAction* AbilityMobilityAction;

	/** Toggles the host rule panel */
	UPROPERTY(EditAnywhere, Category="Input|Abilities")
	UInputAction* RulePanelAction;

	/** Swaps the viewed camera between the shared two-player camera and this character's follow camera */
	UPROPERTY(EditAnywhere, Category="Input")
	UInputAction* ToggleCameraAction;

public:

	/** Constructor */
	AWishCraftCharacter();	

	/** Health accessor, used by damage and match flow */
	UFUNCTION(BlueprintPure, Category="Health")
	UHealthComponent* GetHealthComponent() const { return HealthComponent; }

	/** Loadout manager on the owning controller, if any */
	UFUNCTION(BlueprintPure, Category="Loadout")
	UAbilityLoadoutManager* GetLoadoutManager() const;

	/**
	 * Swaps the viewed camera between the ASharedCameraManager placed in the level and this character's
	 * own follow camera. This is a per-machine preference: each player picks their own view, so it stays
	 * local and never replicates. Defaults to the shared camera. Also an Exec, so a capture pass can
	 * switch views from the console.
	 */
	UFUNCTION(BlueprintCallable, Exec, Category="Camera")
	void ToggleSharedCamera();

	/** True while the shared framing camera owns the view on THIS machine. Mirrors the local preference. */
	UPROPERTY(BlueprintReadOnly, Category="Camera")
	bool bUsingSharedCamera = true;

	/** Server only: replicates so every machine hides the same avatar. */
	UFUNCTION(BlueprintCallable, Category="Ability")
	void SetInvisible(bool bNewInvisible);

	/** True while the invisibility ability is running, on the server and on every client. */
	UFUNCTION(BlueprintPure, Category="Ability")
	bool IsInvisible() const { return bInvisible; }

	/** Server only: replicates an ability's visual half to every machine that can see this character. */
	UFUNCTION(BlueprintCallable, Category="Ability")
	void PlayAbilityEffect(FName Token, bool bStarting);

	/**
	 * Plays this token's configured VFX on THIS machine, reading the effect from the token table.
	 * Called from MulticastAbilityEffect, so it runs on the server and on every client that can see this
	 * character - which is what makes an effect visible to the other player and not only to its caster.
	 */
	void PlayTokenEffect(FName Token, bool bStarting);

	/**
	 * Plays this token's configured audio on THIS machine, from the same fields the VFX use.
	 * Kept separate from PlayTokenEffect so the two halves can be read and changed independently.
	 */
	void PlayTokenAudio(FName Token, bool bStarting);

	/** Viewer-side hook. The ability components are derived from a loadout that only replicates to its
	 *  owner, so a machine that does not own this character has no ability component to notify. */
	UFUNCTION(BlueprintImplementableEvent, Category="Ability", meta = (DisplayName = "On Ability Effect"))
	void BP_OnAbilityEffect(FName Token, bool bStarting);

	/**
	 * Damage feedback. Bound to the health component's OnHealthChanged, which fires on the server from
	 * ApplyDamage and on every client from OnRep_Health - so the reaction is local to each machine and
	 * needs no extra replication of its own.
	 */
	UFUNCTION()
	void HandleHealthChanged(float NewHealth, float MaxHealth);

protected:

	/** Plays the spawn-in effect on every machine that can see this character. */
	void PlaySpawnFeedback();

	/** Replicates the death dissolve so the other player sees it, not only the server. */
	UFUNCTION(NetMulticast, Unreliable)
	void MulticastDeathFeedback();

	/** One-shot impact reaction, played on every machine that can see this character. */
	UPROPERTY(EditDefaultsOnly, Category = "Feedback")
	TObjectPtr<UNiagaraSystem> HitEffect;

	UPROPERTY(EditDefaultsOnly, Category = "Feedback")
	TObjectPtr<USoundBase> HitSound;

	UPROPERTY(EditDefaultsOnly, Category = "Feedback")
	TObjectPtr<UNiagaraSystem> DeathEffect;

	UPROPERTY(EditDefaultsOnly, Category = "Feedback")
	TObjectPtr<USoundBase> DeathSound;

	UPROPERTY(EditDefaultsOnly, Category = "Feedback")
	TObjectPtr<UNiagaraSystem> SpawnEffect;

	UPROPERTY(EditDefaultsOnly, Category = "Feedback")
	TObjectPtr<USoundBase> SpawnSound;

	/** Looping audio for tokens that define a SustainSound, keyed by token so it can be stopped. */
	UPROPERTY(Transient)
	TMap<FName, TObjectPtr<UAudioComponent>> TokenAudio;

	/**
	 * Last health value this machine saw. A hit is a decrease; OnRep_Health also fires for a respawn
	 * refill and for the first replicated value, and neither should spawn an impact effect.
	 */
	float LastKnownHealth = -1.f;

	/** Binds the death broadcast on the server */
	virtual void BeginPlay() override;

	/**
	 * Possession lands after a pawn's BeginPlay on the server, and through a replicated property on a
	 * client, so this is the first moment either machine can point its own controller at the right
	 * view target. Fires again on unpossess, when there is no controller - the handler tolerates that.
	 */
	virtual void NotifyControllerChanged() override;

	/** Points this machine's controller at the camera this player prefers. Local controllers only. */
	void ApplyPreferredCamera();

	/** Retries until the shared camera actor exists here: it is a separate actor and replicates apart. */
	FTimerHandle CameraApplyTimer;

	/** Replicates the invisibility flag */
	virtual void GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const override;

	/** Applies the current invisibility state to the avatar mesh */
	void ApplyInvisibleVisual();

	UFUNCTION()
	void OnRep_Invisible();

	/** Sends an ability cosmetic to every client that can see this character */
	UFUNCTION(NetMulticast, Reliable)
	void MulticastAbilityEffect(FName Token, bool bStarting);

	/** Replicated so every client hides the correct avatar, not only the one it owns */
	UPROPERTY(ReplicatedUsing = OnRep_Invisible, BlueprintReadOnly, Category="Ability")
	bool bInvisible = false;

	/**
	 * Looping effects currently attached, keyed by token, so the sustain can be cleared when the ability
	 * ends. Only the sustain is tracked: the one-shot start and end effects are spawned with auto-destroy
	 * on and need no bookkeeping.
	 */
	UPROPERTY(Transient)
	TMap<FName, TObjectPtr<UNiagaraComponent>> TokenEffects;

	/** Initialize input action bindings */
	virtual void SetupPlayerInputComponent(class UInputComponent* PlayerInputComponent) override;

protected:

	/** Called for movement input */
	void Move(const FInputActionValue& Value);

	/** Called for looking input */
	void Look(const FInputActionValue& Value);

	/** Ability slot handlers */
	void OnAbilityOffense();
	void OnAbilityDefense();
	void OnAbilityMobility();
	void OnRulePanel();

	/** Asks the server to activate whatever is equipped in this category */
	void ActivateCategory(EAbilityCategory Category);

	/** Server-side death notification */
	UFUNCTION()
	void HandleDeath(AActor* Killer);

public:

	/** Handles move inputs from either controls or UI interfaces */
	UFUNCTION(BlueprintCallable, Category="Input")
	virtual void DoMove(float Right, float Forward);

	/** Handles look inputs from either controls or UI interfaces */
	UFUNCTION(BlueprintCallable, Category="Input")
	virtual void DoLook(float Yaw, float Pitch);

	/** Handles jump pressed inputs from either controls or UI interfaces */
	UFUNCTION(BlueprintCallable, Category="Input")
	virtual void DoJumpStart();

	/** Handles jump pressed inputs from either controls or UI interfaces */
	UFUNCTION(BlueprintCallable, Category="Input")
	virtual void DoJumpEnd();

public:

	/** Returns CameraBoom subobject **/
	FORCEINLINE class USpringArmComponent* GetCameraBoom() const { return CameraBoom; }

	/** Returns FollowCamera subobject **/
	FORCEINLINE class UCameraComponent* GetFollowCamera() const { return FollowCamera; }
};
